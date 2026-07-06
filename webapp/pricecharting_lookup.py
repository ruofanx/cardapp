"""
PriceCharting graded-card pricing.

PriceCharting publishes a per-card price table covering Ungraded, generic
Grade 7/8/9/9.5, and grader-specific PSA/CGC/BGS/SGC 10s plus exotic 'BGS 10
Black' / 'CGC 10 Pristine' rows. We scrape that table and cache results
locally for 24 hours.

Why scrape: PriceCharting's structured API costs $40/mo. The HTML page is
public and stable enough that a regex parser is reliable. Cache aggressively
to avoid hammering them.

URL pattern (English):
  https://www.pricecharting.com/game/pokemon-<set-slug>/<name-slug>-<number>

URL pattern (Japanese):
  https://www.pricecharting.com/game/pokemon-japanese-<set-slug>/<name-slug>-<number>

Slug guessing isn't perfect; we try a few variants per lookup.
"""
from __future__ import annotations

import html
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

log = logging.getLogger(__name__)

PC_BASE = "https://www.pricecharting.com"
CACHE_DB = Path(__file__).parent / "pricecharting_cache.sqlite"
CACHE_TTL_SECONDS = 24 * 3600  # 1 day

# Map (grader, grade) → row label PriceCharting uses on the card page.
# PriceCharting has authoritative grader-specific rows only at grade 10
# (PSA 10, CGC 10, BGS 10, SGC 10, plus rare BGS 10 Black / CGC 10 Pristine).
# For sub-10 grades they expose a generic cross-service "Grade N" row;
# we map all sub-10 grades to that, accepting some loss of precision.
GRADE_TO_PC_ROW = {
    ("PSA", 10):    "PSA 10",
    ("CGC", 10):    "CGC 10",
    ("BGS", 10):    "BGS 10",
    ("SGC", 10):    "SGC 10",
    ("PSA", 9.5):   "Grade 9.5",
    ("PSA", 9):     "Grade 9",
    ("PSA", 8):     "Grade 8",
    ("PSA", 7):     "Grade 7",
    ("CGC", 9.5):   "Grade 9.5",
    ("CGC", 9):     "Grade 9",
    ("CGC", 8):     "Grade 8",
    ("BGS", 9.5):   "Grade 9.5",
    ("BGS", 9):     "Grade 9",
    ("BGS", 8):     "Grade 8",
    ("SGC", 9.5):   "Grade 9.5",
    ("SGC", 9):     "Grade 9",
    ("SGC", 8):     "Grade 8",
}

SEALED_PRODUCT_SLUGS: dict[str, str] = {
    "booster_box":  "booster-box",
    "etb":          "elite-trainer-box",
    "booster_pack": "booster-pack",
    "tin":          "tin",
    "bundle":       "booster-bundle",
}

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

# <tr><td>LABEL</td><td class="price js-price">$N.NN</td></tr>
_PRICE_ROW_RE = re.compile(
    r'<tr>\s*<td>([^<]+)</td>\s*<td[^>]*class="[^"]*\bprice\b[^"]*\bjs-price\b[^"]*"[^>]*>\s*\$?([0-9.,\-]+)\s*</td>',
    re.IGNORECASE,
)

# Every PriceCharting product page embeds its price-history graph data inline:
#   VGPC.chart_data = {"used": [[<ms timestamp>, <price in cents>], ...], "graded": [...], ...}
# "used" is PriceCharting's loose/ungraded series — it lines up with the
# "Ungraded" row `lookup_raw_price` reads from the price table (verified:
# Venusaur ex 198/165 chart's latest "used" point ($120.24) ≈ live
# "Ungraded" price ($120.23)). Spans ~3 years at roughly monthly granularity,
# far more history than this app has collected on its own (~3 weeks).
_CHART_DATA_RE = re.compile(r'VGPC\.chart_data\s*=\s*(\{.*?\});', re.DOTALL)

# Every PriceCharting product page also embeds a cover-art image:
#   <div class="cover"><a href="#"><img src='https://storage.googleapis.com/images.pricecharting.com/{hash}/{size}.jpg' alt="...">
# This is official Pokemon TCG box art — much higher quality than eBay
# active-listing seller photos (used as the image source for sealed
# products). {hash} is stable per product; confirmed available sizes are
# 240 (thumbnail) and 1600 (full resolution).
_COVER_IMAGE_RE = re.compile(
    r'<div class="cover">.*?<img src=[\'"]([^\'"]+?)/\d+\.jpg[\'"]',
    re.IGNORECASE | re.DOTALL,
)

# `/search-products?type=prices&q=...` returns a results table — one
# `<tr id="product-{id}" ...>` per product. Split on that marker so each
# row can be parsed independently (a row missing a piece, e.g. no price,
# shouldn't pull fields from its neighbour).
_SEARCH_ROW_SPLIT_RE = re.compile(r'<tr id="product-(\d+)"')
_SEARCH_TITLE_RE = re.compile(
    r'<td class="title">\s*<a href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)
# Same console link appears twice per row (once inside the title cell, once
# in its own "console" cell) — either match gives the set slug + name.
_SEARCH_SET_RE = re.compile(
    r'<a href="/console/([^"]+)">\s*([^<]+?)\s*</a>',
    re.IGNORECASE | re.DOTALL,
)
_SEARCH_PRICE_RE = re.compile(r'<span class="js-price">\$?([\d,.]+)</span>')
_SEARCH_IMAGE_RE = re.compile(r'<img class="photo"[^>]*\bsrc="([^"]+?)/\d+\.\w+"')
# "Maushold Ex #158" -> ("Maushold Ex", "158"); titles without a printed
# number (sealed products) leave card_number as None.
_TITLE_NUMBER_RE = re.compile(r'^(.*?)\s*#(\S+)$')


@dataclass
class PriceChartingResult:
    url: str
    grade_label: str           # e.g. "PSA 10" or "Grade 9.5"
    price_usd: Optional[float]
    all_prices: dict[str, float]   # full price table, label → USD
    cached: bool
    image_url: Optional[str] = None        # cover art, 240px
    image_url_large: Optional[str] = None  # cover art, 1600px


@dataclass
class PriceChartingSearchResult:
    """One row from `/search-products` — a card-identity fallback for sets
    TCGdex has registered but never populated (e.g. Chinese-exclusive
    "CSV4C" / 嘉奖回合)."""
    product_id: str
    name: str
    card_number: Optional[str]
    set_name: str
    url: str
    price_usd: Optional[float]
    language: str = "english"              # "chinese" | "japanese" | "english"
    image_url: Optional[str] = None        # cover art, 240px
    image_url_large: Optional[str] = None  # cover art, 1600px

    def to_dict(self):
        return {
            "id": f"pricecharting-{self.product_id}",
            "name": self.name,
            "card_number": self.card_number,
            "set_name": self.set_name,
            "language": self.language,
            "market_price": self.price_usd,
            "image_url": self.image_url_large or self.image_url,
            "image_url_small": self.image_url,
            "source": "pricecharting",
            "pricecharting_url": self.url,
        }


# ---------------------------------------------------------------------------
# Slug + URL construction
# ---------------------------------------------------------------------------

# Japanese set name → PriceCharting set slug (used directly; no further slugification).
# Keys are lowercased set names as they appear in the DB (both JP-character and
# English transliterations). Values are the exact slug segment that follows
# "pokemon-japanese-" in the PC URL.
_JP_SET_PC_SLUG_MAP: dict[str, str] = {
    # Pokemon Card 151 (SV2a) — PC uses "Scarlet & Violet 151" naming
    "ポケモンカード151":            "scarlet-&-violet-151",
    "pokemon card 151":             "scarlet-&-violet-151",
    "card 151":                     "scarlet-&-violet-151",
    "151":                          "scarlet-&-violet-151",
    # Terastal Festival ex (SV4a)
    "テラスタルフェスex":           "terastal-festival",
    "terastal festival ex":         "terastal-festival",
    "terastal festival":            "terastal-festival",
    # Shiny Treasure ex (SV4a bonus set)
    "シャイニートレジャーex":       "shiny-treasure-ex",
    "shiny treasure ex":            "shiny-treasure-ex",
    # Clay Burst / Snow Hazard (SV2D/SV2P)
    "クレイバースト":               "clay-burst",
    "clay burst":                   "clay-burst",
    "スノーハザード":               "snow-hazard",
    "snow hazard":                  "snow-hazard",
    # Triplet Beat (SV1a)
    "トリプレットビート":           "triplet-beat",
    "triplet beat":                 "triplet-beat",
    # Violet ex / Scarlet ex (SV1S/SV1V)
    "スカーレットex":               "scarlet-ex",
    "ヴァイオレットex":             "violet-ex",
    "scarlet ex":                   "scarlet-ex",
    "violet ex":                    "violet-ex",
    # Future Flash / Ancient Roar (SV4M/SV4K)
    "フューチャーフラッシュ":       "future-flash",
    "エンシェントロアー":           "ancient-roar",
    "future flash":                 "future-flash",
    "ancient roar":                 "ancient-roar",
    # Crimson Haze (SV5A)
    "クリムゾンヘイズ":             "crimson-haze",
    "crimson haze":                 "crimson-haze",
    # Twilight Masquerade (SV6) / Stellar Crown (SV7)
    "ナイトワンダラー":             "night-wanderer",
    "night wanderer":               "night-wanderer",
    # VSTAR Universe (S12a)
    "vstar universe":               "vstar-universe",
    # Nihil Zero (SV3P)
    "ニルゼロ":                     "nihil-zero",
    "nihil zero":                   "nihil-zero",
    # Classic JP sets — Pokédex-numbered on PriceCharting
    "金、銀、新世界へ...":           "gold-silver-new-world",  # JP Neo Genesis
    "gold, silver, new world":      "gold-silver-new-world",
    "gold silver new world":        "gold-silver-new-world",
    "expansionpack":                "expansion-pack",          # JP Base Set
    "expansion pack":               "expansion-pack",
    "jungle":                       "jungle",
    "fossil":                       "fossil",
    "team rocket":                  "team-rocket",
}

# Japanese card name → English slug fragment used in PriceCharting URLs.
# Maps base names only (suffixes like "ex", "GX", "VSTAR" are handled separately).
_JP_CARD_NAME_EN_MAP: dict[str, str] = {
    # Common Pokémon
    "フシギダネ": "bulbasaur", "フシギソウ": "ivysaur", "フシギバナ": "venusaur",
    "ヒトカゲ": "charmander", "リザード": "charmeleon", "リザードン": "charizard",
    "ゼニガメ": "squirtle", "カメール": "wartortle", "カメックス": "blastoise",
    "ピカチュウ": "pikachu", "ライチュウ": "raichu", "ピチュー": "pichu",
    "ミュウ": "mew", "ミュウツー": "mewtwo",
    "ゲンガー": "gengar", "ゴース": "gastly", "ゴースト": "haunter",
    "カイリュー": "dragonite", "ミニリュウ": "dratini", "ハクリュー": "dragonair",
    "ラプラス": "lapras", "エビワラー": "hitmonchan", "サワムラー": "hitmonlee",
    "ルギア": "lugia", "ホウオウ": "ho-oh",
    "ピィ": "cleffa", "ピクシー": "clefable", "ピッピ": "clefairy",
    "ニョロボン": "poliwrath", "ニョロゲロ": "poliwag", "ニョロゾ": "poliwhirl",
    "ヤドン": "slowpoke", "ヤドラン": "slowbro", "ヤドキング": "slowking",
    "イーブイ": "eevee",
    "シャワーズ": "vaporeon", "サンダース": "jolteon", "ブースター": "flareon",
    "エーフィ": "espeon", "ブラッキー": "umbreon",
    "グレイシア": "glaceon", "リーフィア": "leafeon", "ニンフィア": "sylveon",
    "ムゲンダイナ": "eternatus", "ザシアン": "zacian", "ザマゼンタ": "zamazenta",
    "フリーザー": "articuno", "サンダー": "zapdos", "ファイヤー": "moltres",
    "ヨルノズク": "noctowl", "デンリュウ": "ampharos", "バンギラス": "tyranitar",
    "ソーナンス": "wobbuffet", "ハピナス": "blissey",
    "ゴローニャ": "golem", "イワーク": "onix", "ハガネール": "steelix",
    "カビゴン": "snorlax", "ヒポポタス": "hippopotas", "ドサイドン": "rhyperior",
    "ゲッコウガ": "greninja", "ニンフィア": "sylveon",
    "ガブリアス": "garchomp", "ルカリオ": "lucario", "リオル": "riolu",
    "ゾロアーク": "zoroark", "ゾロア": "zorua",
    "ギャラドス": "gyarados", "コイキング": "magikarp",
    "ドラパルト": "dragapult", "ヒドイデ": "mareanie", "ドヒドイデ": "toxapex",
    "イシヘンジン": "stonjourner",
    "レシラム": "reshiram", "ゼクロム": "zekrom", "キュレム": "kyurem",
    "コイキング": "magikarp",
    "ソルガレオ": "solgaleo", "ルナアーラ": "lunala", "ネクロズマ": "necrozma",
    "マジコスサーナイト": "gardevoir-ex",
    "サーナイト": "gardevoir",
    "ブリガロン": "chesnaught", "マフォクシー": "delphox", "ゲッコウガ": "greninja",
    "ドラゴナイト": "dragonite",
    "キングドラ": "kingdra", "ヤングース": "yungoos",
    "イキリンコ": "squawkabilly", "マスカーニャ": "meowscarada",
    "ホゲータ": "fuecoco", "ラウドボーン": "skeledirge",
    "クワッス": "quaxly", "ウェーニバル": "quaquaval",
    "ニャオハ": "sprigatito", "コライドン": "koraidon", "ミライドン": "miraidon",
    "ウミトリオ": "palafin", "ウパー": "wooper", "ヌオー": "quagsire",
    "パフュートン": "appletun", "ザロードン": "applin",
    "テツノカイナ": "iron-hands", "テツノドクガ": "iron-moth",
    "テツノイサハ": "iron-leaves", "テツノコウベ": "iron-crown",
    "サケブシッポ": "flutter-mane", "チヲハウハネ": "chien-pao",
    "イダイナキバ": "great-tusk", "テツノブジン": "iron-valiant",
    "ハバタクカミ": "flutter-mane",
    "ウネルミナモ": "walking-wake", "テツノワダチ": "iron-treads",
    "カミッチュ": "comfey",
    # Legendary / Mythical
    "ケルディオ": "keldeo", "メロエッタ": "meloetta", "ゲノセクト": "genesect",
    "ディアルガ": "dialga", "パルキア": "palkia", "ギラティナ": "giratina",
    "グラードン": "groudon", "カイオーガ": "kyogre", "レックウザ": "rayquaza",
    "アルセウス": "arceus", "ダークライ": "darkrai", "シェイミ": "shaymin",
    "セレビィ": "celebi", "エンテイ": "entei", "ライコウ": "raikou",
    "スイクン": "suicune", "クレセリア": "cresselia",
    "ビクティニ": "victini", "ランドロス": "landorus", "テッシード": "ferroseed",
    "ジラーチ": "jirachi", "デオキシス": "deoxys",
    "カプ・テテフ": "tapu-lele", "カプ・コケコ": "tapu-koko",
    "カプ・レヒレ": "tapu-fini", "カプ・ブルル": "tapu-bulu",
    "ザルード": "zarude", "マナフィ": "manaphy", "フィオネ": "phione",
    "ウルガモス": "volcarona", "シャンデラ": "chandelure",
    "テールナー": "litwick", "ランプラー": "lampent",
    # Common non-Pokémon cards
    "博士の研究": "professors-research",
    "マリィ": "marnie",
    "ボスの指令": "bosss-orders",
}

# Suffixes to separate from the base Japanese name before mapping.
_JP_NAME_SUFFIXES = re.compile(
    r'^(.*?)(ex|EX|GX|V(?:MAX|STAR|-UNION)?|Tag Team)$', re.IGNORECASE
)

# PriceCharting set slugs (the part after "pokemon-japanese-") that use the
# National Pokédex number as the card identifier instead of the set card number.
# TCGdex sometimes stores sequential localIds for these old JP sets, so we
# generate an extra candidate URL using the Pokédex number.
_PC_POKEDEX_NUM_SETS: frozenset[str] = frozenset({
    "expansion-pack",       # JP Base Set
    "jungle",               # JP Jungle
    "fossil",               # JP Fossil
    "team-rocket",          # JP Team Rocket
    "gold-silver-new-world",# JP Neo Genesis
    "southern-islands",     # JP Southern Islands
})

# English card name → National Pokédex number (Gen 1–2 + select others).
# Used to generate Pokédex-numbered PriceCharting URLs for old JP sets.
_EN_NAME_TO_POKEDEX: dict[str, int] = {
    "bulbasaur":1,"ivysaur":2,"venusaur":3,"charmander":4,"charmeleon":5,
    "charizard":6,"squirtle":7,"wartortle":8,"blastoise":9,"caterpie":10,
    "metapod":11,"butterfree":12,"weedle":13,"kakuna":14,"beedrill":15,
    "pidgey":16,"pidgeotto":17,"pidgeot":18,"rattata":19,"raticate":20,
    "spearow":21,"fearow":22,"ekans":23,"arbok":24,"pikachu":25,
    "raichu":26,"sandshrew":27,"sandslash":28,"nidoran-f":29,"nidorina":30,
    "nidoqueen":31,"nidoran-m":32,"nidorino":33,"nidoking":34,"clefairy":35,
    "clefable":36,"vulpix":37,"ninetales":38,"jigglypuff":39,"wigglytuff":40,
    "zubat":41,"golbat":42,"oddish":43,"gloom":44,"vileplume":45,
    "paras":46,"parasect":47,"venonat":48,"venomoth":49,"diglett":50,
    "dugtrio":51,"meowth":52,"persian":53,"psyduck":54,"golduck":55,
    "mankey":56,"primeape":57,"growlithe":58,"arcanine":59,"poliwag":60,
    "poliwhirl":61,"poliwrath":62,"abra":63,"kadabra":64,"alakazam":65,
    "machop":66,"machoke":67,"machamp":68,"bellsprout":69,"weepinbell":70,
    "victreebel":71,"tentacool":72,"tentacruel":73,"geodude":74,"graveler":75,
    "golem":76,"ponyta":77,"rapidash":78,"slowpoke":79,"slowbro":80,
    "magnemite":81,"magneton":82,"farfetchd":83,"doduo":84,"dodrio":85,
    "seel":86,"dewgong":87,"grimer":88,"muk":89,"shellder":90,
    "cloyster":91,"gastly":92,"haunter":93,"gengar":94,"onix":95,
    "drowzee":96,"hypno":97,"krabby":98,"kingler":99,"voltorb":100,
    "electrode":101,"exeggcute":102,"exeggutor":103,"cubone":104,"marowak":105,
    "hitmonlee":106,"hitmonchan":107,"lickitung":108,"koffing":109,"weezing":110,
    "rhyhorn":111,"rhydon":112,"chansey":113,"tangela":114,"kangaskhan":115,
    "horsea":116,"seadra":117,"goldeen":118,"seaking":119,"staryu":120,
    "starmie":121,"mr-mime":122,"scyther":123,"jynx":124,"electabuzz":125,
    "magmar":126,"pinsir":127,"tauros":128,"magikarp":129,"gyarados":130,
    "lapras":131,"ditto":132,"eevee":133,"vaporeon":134,"jolteon":135,
    "flareon":136,"porygon":137,"omanyte":138,"omastar":139,"kabuto":140,
    "kabutops":141,"aerodactyl":142,"snorlax":143,"articuno":144,"zapdos":145,
    "moltres":146,"dratini":147,"dragonair":148,"dragonite":149,"mewtwo":150,
    "mew":151,
    # Gen 2
    "chikorita":152,"bayleef":153,"meganium":154,"cyndaquil":155,"quilava":156,
    "typhlosion":157,"totodile":158,"croconaw":159,"feraligatr":160,
    "sentret":161,"furret":162,"hoothoot":163,"noctowl":164,"ledyba":165,
    "ledian":166,"spinarak":167,"ariados":168,"crobat":169,"chinchou":170,
    "lanturn":171,"pichu":172,"cleffa":173,"igglybuff":174,"togepi":175,
    "togetic":176,"natu":177,"xatu":178,"mareep":179,"flaaffy":180,
    "ampharos":181,"bellossom":182,"marill":183,"azumarill":184,"sudowoodo":185,
    "politoed":186,"hoppip":187,"skiploom":188,"jumpluff":189,"aipom":190,
    "sunkern":191,"sunflora":192,"yanma":193,"wooper":194,"quagsire":195,
    "espeon":196,"umbreon":197,"murkrow":198,"slowking":199,"misdreavus":200,
    "unown":201,"wobbuffet":202,"girafarig":203,"pineco":204,"forretress":205,
    "dunsparce":206,"gligar":207,"steelix":208,"snubbull":209,"granbull":210,
    "qwilfish":211,"scizor":212,"shuckle":213,"heracross":214,"sneasel":215,
    "teddiursa":216,"ursaring":217,"slugma":218,"magcargo":219,"swinub":220,
    "piloswine":221,"corsola":222,"remoraid":223,"octillery":224,"delibird":225,
    "mantine":226,"skarmory":227,"houndour":228,"houndoom":229,"kingdra":230,
    "phanpy":231,"donphan":232,"porygon2":233,"stantler":234,"smeargle":235,
    "tyrogue":236,"hitmontop":237,"smoochum":238,"elekid":239,"magby":240,
    "miltank":241,"blissey":242,"raikou":243,"entei":244,"suicune":245,
    "larvitar":246,"pupitar":247,"tyranitar":248,"lugia":249,"ho-oh":250,
    "celebi":251,
}


def _jp_name_to_en_slug(name: str) -> Optional[str]:
    """Convert a Japanese card name to an English slug for PriceCharting.

    Tries exact match first; then splits trailing suffixes (ex, GX, VSTAR…)
    and maps the base name independently. Returns None when the name is
    ASCII-only (no conversion needed) or not in the mapping table.
    """
    if name.isascii():
        return None  # Already ASCII — caller handles normally
    lower = name.lower()
    if lower in _JP_CARD_NAME_EN_MAP:
        return _JP_CARD_NAME_EN_MAP[lower]
    # Strip trailing ASCII suffix and retry
    m = _JP_NAME_SUFFIXES.match(name)
    if m:
        base_jp = m.group(1).strip()
        suffix  = m.group(2).lower()
        base_en = _JP_CARD_NAME_EN_MAP.get(base_jp.lower())
        if base_en:
            return f"{base_en}-{suffix}"
    return None


def _slug(s: str) -> str:
    s = s.lower()
    s = re.sub(r"['.,:!()&]", "", s)
    s = re.sub(r"\s+", "-", s.strip())
    s = re.sub(r"-+", "-", s)
    return s.strip("-")


def _normalize_card_number(num: str) -> str:
    """For the URL we want '174', '25', or 'TG11' — drop the /total and
    leading zeros for purely-numeric numbers."""
    head = num.split("/")[0].strip()
    if head.isdigit():
        return str(int(head))
    return head


def _candidate_urls(name: str, set_name: str, card_number: str,
                    language: str = "english",
                    variant: Optional[str] = None) -> list[str]:
    """Generate plausible PriceCharting URLs to try in order.

    PriceCharting's slug scheme is annoyingly card-specific:
      - Some cards have ONE page that covers all prints (Fossil Gengar at
        `gengar-5` has Unlimited Holo data; there's no `gengar-holo-5`).
      - Other cards have SEPARATE pages per print: Team Rocket Dark Dragonite
        has `dark-dragonite-5` (non-holo) AND `dark-dragonite-holo-5` (Holo
        Unlimited, the one whose PSA 9 trades at ~$445).
      - 1st Edition is usually a separate page: `dark-dragonite-1st-edition-5`.

    We can't predict which form a given card uses, so we generate the
    likely candidates and try them in order. The fetcher skips any URL
    that redirects to `/search-products` (PriceCharting's "no such page"
    behaviour), so dead variants drop out automatically.
    """
    # --- Card name slug ----------------------------------------------------------
    # For JP cards whose name is stored in Japanese, translate to EN for PC.
    en_slug_from_jp = _jp_name_to_en_slug(name)
    name_slug = en_slug_from_jp if en_slug_from_jp is not None else _slug(name)

    num = _normalize_card_number(card_number)

    # --- Set slug ----------------------------------------------------------------
    raw_set = (set_name or "").strip()

    # Check the override map first: covers JP-only sets whose PC slug can't be
    # derived from the stored name (e.g. ポケモンカード151 → scarlet-&-violet-151,
    # "Pokemon Card 151" → same, "Pokemon Japanese Terastal Festival" → terastal-festival).
    # Strip common prefixes before lookup so "Pokemon Japanese Terastal Festival"
    # and "Terastal Festival" both hit the same key.
    _raw_lower = raw_set.lower()
    # Strip "Pokemon Japanese " / "Pokemon " prefix for DB entries that include it
    _raw_stripped = re.sub(r"^pokemon\s+japanese\s+", "", _raw_lower).strip()
    _raw_stripped = re.sub(r"^pokemon\s+", "", _raw_stripped).strip()
    _forced_slug: Optional[str] = (
        _JP_SET_PC_SLUG_MAP.get(_raw_lower)
        or _JP_SET_PC_SLUG_MAP.get(_raw_stripped)
        or _JP_SET_PC_SLUG_MAP.get(_raw_lower.replace("ポケモン", "").strip())
    )
    if _forced_slug:
        base_slugs = [_forced_slug]
    else:
        # Standard derivation — strip common set-name prefixes then slugify.
        set_clean = re.sub(
            r"^(SV|S&V|Scarlet\s*&\s*Violet|SWSH|Sword\s*&\s*Shield|Pokemon\s+Japanese\s+)[:\s]*",
            "", raw_set, flags=re.IGNORECASE,
        ).strip()
        set_no_tg = re.sub(r"\s+Trainer\s+Gallery\s*$", "", set_clean, flags=re.IGNORECASE).strip()
        base_slugs = list(dict.fromkeys([_slug(s) for s in (set_clean, set_no_tg, raw_set) if s]))

    # ----- Detect variant flags from the user/LLM-provided variant string ----
    v = (variant or "").strip().lower()
    is_holo       = "holo" in v and "reverse" not in v
    is_first_ed   = "1st" in v or "first edition" in v
    is_shadowless = "shadowless" in v
    is_reverse    = "reverse" in v
    is_master_ball = "master ball" in v

    # ----- Name-slug qualifiers: try most specific first ----------------------
    # The qualifier is inserted between the card name and the number, e.g.
    # `dark-dragonite-holo-5` or `dark-dragonite-1st-edition-5`. We always
    # include the plain `name-num` as a final fallback because some cards
    # (Fossil Gengar) put all data on the default page.
    name_qualifiers: list[str] = []
    if is_first_ed and is_holo:
        name_qualifiers += ["1st-edition-holo", "holo-1st-edition", "1st-edition", "holo"]
    elif is_first_ed:
        name_qualifiers += ["1st-edition"]
    elif is_holo:
        name_qualifiers += ["holo"]
    elif is_shadowless:
        name_qualifiers += ["shadowless"]
    elif is_reverse:
        name_qualifiers += ["reverse-holo"]
    elif is_master_ball:
        # Prismatic Evolutions Master Ball pattern has its own PC page:
        # e.g. pokemon-prismatic-evolutions/umbreon-master-ball-59
        name_qualifiers += ["master-ball"]
    name_qualifiers.append("")    # plain name-num — final fallback

    # ----- Set-slug variants: some cards use `set-1st-edition` instead --------
    set_variant_suffix = ""
    if v in ("1st edition", "first edition"):
        set_variant_suffix = "-1st-edition"
    elif v == "shadowless":
        set_variant_suffix = "-shadowless"

    set_variants: list[str] = []
    if set_variant_suffix:
        set_variants.extend(s + set_variant_suffix for s in base_slugs)
    set_variants.extend(base_slugs)
    seen = set()
    set_variants = [s for s in set_variants if not (s in seen or seen.add(s))]

    jp_prefix = "japanese-" if language.lower() == "japanese" else ""

    # For old JP sets where PriceCharting uses the National Pokédex number as
    # the card identifier (instead of the printed set card number), generate an
    # extra candidate using the Pokédex number.  TCGdex uses sequential IDs for
    # these sets (e.g. neo1-072 for Lugia) which don't match PC's Pokédex-
    # numbered URLs (lugia-249).  We try both so the fetcher can pick the hit.
    pokedex_num: Optional[str] = None
    if any(s in _PC_POKEDEX_NUM_SETS for s in set_variants):
        # Look up the Pokédex number from the English name slug we already have.
        dex = _EN_NAME_TO_POKEDEX.get(name_slug)
        if dex is None and en_slug_from_jp:
            # Strip trailing suffixes (ex-217 → just the base name) then look up
            dex = _EN_NAME_TO_POKEDEX.get(en_slug_from_jp.split("-")[0])
        if dex is not None:
            pokedex_num = str(dex)

    # Build URLs: outer loop set variants, inner loop name qualifiers
    urls: list[str] = []
    for set_slug in set_variants:
        for qual in name_qualifiers:
            # For old JP sets PriceCharting keys on National Pokédex number, not
            # the printed card number.  Try the Pokédex URL *first* so we don't
            # accidentally match a different product at the card-number slot.
            if pokedex_num and pokedex_num != num and set_slug in _PC_POKEDEX_NUM_SETS:
                if qual:
                    urls.append(f"{PC_BASE}/game/pokemon-{jp_prefix}{set_slug}/{name_slug}-{qual}-{pokedex_num}")
                else:
                    urls.append(f"{PC_BASE}/game/pokemon-{jp_prefix}{set_slug}/{name_slug}-{pokedex_num}")
            if qual:
                url = f"{PC_BASE}/game/pokemon-{jp_prefix}{set_slug}/{name_slug}-{qual}-{num}"
            else:
                url = f"{PC_BASE}/game/pokemon-{jp_prefix}{set_slug}/{name_slug}-{num}"
            urls.append(url)
    # Final dedupe preserving order
    out_seen = set()
    return [u for u in urls if not (u in out_seen or out_seen.add(u))]


# Sealed products for OLD sets (Base Set, Jungle, Fossil, Team Rocket, ...)
# are commonly entered with a trailing print-run qualifier in `set_name`,
# e.g. "Jungle Unlimited" or "Fossil 1st Edition" — but PriceCharting
# catalogues sealed booster packs/boxes/etc. under the base set name
# (`pokemon-jungle/booster-pack`, not `pokemon-jungle-unlimited/booster-pack`,
# which redirects to /search-products). Strip a trailing "Unlimited"/
# "1st Edition"/"First Edition" qualifier as a fallback candidate.
_SET_PRINT_RUN_RE = re.compile(
    r"\s+(?:1st edition|first edition|unlimited)\s*$", re.IGNORECASE,
)


def _sealed_product_urls(name: str, set_name: str, product_type: str,
                          language: str = "english") -> list[str]:
    """Generate plausible PriceCharting sealed-product URLs to try in order.

    Tries `set_name` (or `name` if `set_name` is empty) as given first, then
    with a trailing print-run qualifier stripped (see `_SET_PRINT_RUN_RE`).
    Returns [] if `product_type` isn't in SEALED_PRODUCT_SLUGS.
    """
    product_slug = SEALED_PRODUCT_SLUGS.get(product_type)
    if product_slug is None:
        return []

    jp_prefix = "japanese-" if language.lower() == "japanese" else ""
    raw = (set_name or name or "").strip()
    candidates = [raw]
    stripped = _SET_PRINT_RUN_RE.sub("", raw).strip()
    if stripped and stripped != raw:
        candidates.append(stripped)

    seen = set()
    set_slugs = [_slug(c) for c in candidates if c and not (c in seen or seen.add(c))]
    return [f"{PC_BASE}/game/pokemon-{jp_prefix}{s}/{product_slug}" for s in set_slugs]


# ---------------------------------------------------------------------------
# Cache (SQLite, 24h TTL)
# ---------------------------------------------------------------------------

def _init_cache() -> None:
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pc_cache (
            url         TEXT PRIMARY KEY,
            prices_json TEXT NOT NULL,
            ts          REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _cache_get(url: str) -> Optional[dict[str, float]]:
    _init_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    row = conn.execute(
        "SELECT prices_json, ts FROM pc_cache WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    if time.time() - row[1] > CACHE_TTL_SECONDS:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def _cache_set(url: str, prices: dict[str, float]) -> None:
    _init_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute(
        "INSERT OR REPLACE INTO pc_cache (url, prices_json, ts) VALUES (?, ?, ?)",
        (url, json.dumps(prices), time.time()),
    )
    conn.commit()
    conn.close()


# Separate small cache for sealed-product cover images, keyed by the same
# product-page URL as `pc_cache`. Kept separate from `prices_json` (typed as
# dict[str, float] everywhere) so the image base URL — a string — doesn't
# need to be smuggled into that price table.
def _init_image_cache() -> None:
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pc_sealed_image_cache (
            url        TEXT PRIMARY KEY,
            image_base TEXT,
            ts         REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _image_cache_get(url: str) -> Optional[str]:
    _init_image_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    row = conn.execute(
        "SELECT image_base, ts FROM pc_sealed_image_cache WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    if not row or time.time() - row[1] > CACHE_TTL_SECONDS:
        return None
    return row[0]


def _image_cache_has(url: str) -> bool:
    """True if url has a fresh row in the image cache (even if image_base is NULL)."""
    _init_image_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    row = conn.execute(
        "SELECT ts FROM pc_sealed_image_cache WHERE url = ?", (url,)
    ).fetchone()
    conn.close()
    return bool(row and time.time() - row[0] <= CACHE_TTL_SECONDS)


def _image_cache_set(url: str, image_base: Optional[str]) -> None:
    _init_image_cache()
    conn = sqlite3.connect(str(CACHE_DB))
    conn.execute(
        "INSERT OR REPLACE INTO pc_sealed_image_cache (url, image_base, ts) VALUES (?, ?, ?)",
        (url, image_base, time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _parse_price_table(html: str) -> dict[str, float]:
    """Extract the per-grade price table from a PriceCharting card page."""
    out: dict[str, float] = {}
    for label, value in _PRICE_ROW_RE.findall(html):
        label = label.strip()
        value = value.strip()
        if value in ("", "-"):
            continue
        try:
            out[label] = float(value.replace(",", ""))
        except ValueError:
            continue
    return out


def _parse_cover_image_base(html: str) -> Optional[str]:
    """Extract the product-cover image base URL from a PriceCharting page.

    Returns e.g. 'https://storage.googleapis.com/images.pricecharting.com/{hash}/'
    (trailing slash) so callers can append '240.jpg' or '1600.jpg'. Returns
    None if the page has no `<div class="cover">` image (not in catalogue).
    """
    m = _COVER_IMAGE_RE.search(html)
    if not m:
        return None
    return m.group(1) + "/"


def _cover_image_urls(image_base: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Expand a cover image base URL into (thumbnail, full-size) variants."""
    if not image_base:
        return None, None
    return image_base + "240.jpg", image_base + "1600.jpg"


def _parse_chart_series(html: str, series: str = "used") -> Optional[list[tuple[int, float]]]:
    """Extract one series from a page's embedded `VGPC.chart_data` as
    [(timestamp_ms, price_usd), ...] sorted ascending. Returns None if
    chart_data is missing/unparseable or the series has no nonzero points.
    "used" is the raw/ungraded line — see `_CHART_DATA_RE` for the verified
    mapping to the "Ungraded"/"Sealed" price-table row.
    """
    m = _CHART_DATA_RE.search(html)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
    points = data.get(series) or []
    cleaned = sorted((int(t), v / 100.0) for t, v in points if v)
    return cleaned or None


def _detect_language(set_slug: str) -> str:
    """PriceCharting consoles encode language in the slug:
    'pokemon-chinese-csv4c', 'pokemon-japanese-future-flash', 'pokemon-paradox-rift'."""
    s = set_slug.lower()
    if "chinese" in s:
        return "chinese"
    if "japanese" in s:
        return "japanese"
    return "english"


def _parse_search_results(html_text: str, limit: int = 10) -> list[PriceChartingSearchResult]:
    """Parse product rows from a `/search-products?type=prices&q=` page.

    Only Pokemon products (console slug starting with "pokemon") are kept —
    a free-text card name can match unrelated video games/merch otherwise.
    """
    out: list[PriceChartingSearchResult] = []
    chunks = _SEARCH_ROW_SPLIT_RE.split(html_text)
    # chunks alternates [pre-text, product_id, row_html, product_id, row_html, ...]
    for i in range(1, len(chunks) - 1, 2):
        product_id, row = chunks[i], chunks[i + 1]
        title_m = _SEARCH_TITLE_RE.search(row)
        set_m = _SEARCH_SET_RE.search(row)
        if not title_m or not set_m:
            continue
        set_slug, set_name = set_m.group(1), html.unescape(set_m.group(2).strip())
        if not set_slug.lower().startswith("pokemon"):
            continue

        url = title_m.group(1)
        title = html.unescape(title_m.group(2).strip())
        num_m = _TITLE_NUMBER_RE.match(title)
        name, card_number = (num_m.group(1).strip(), num_m.group(2)) if num_m else (title, None)

        price_m = _SEARCH_PRICE_RE.search(row)
        price = float(price_m.group(1).replace(",", "")) if price_m else None

        img_m = _SEARCH_IMAGE_RE.search(row)
        image_url, image_url_large = _cover_image_urls(img_m.group(1) + "/") if img_m else (None, None)

        out.append(PriceChartingSearchResult(
            product_id=product_id, name=name, card_number=card_number,
            set_name=set_name, url=url, price_usd=price,
            language=_detect_language(set_slug),
            image_url=image_url, image_url_large=image_url_large,
        ))
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def lookup_raw_price(name: str, set_name: str, card_number: str,
                            language: str = "english",
                            variant: Optional[str] = None) -> Optional[PriceChartingResult]:
    """Pull the 'Ungraded' price from PriceCharting for this card.

    `variant` matters for OLD prints: 1st Edition and Shadowless live on
    separate PC pages and trade at different prices. Pass it through so
    the URL builder prefers the right slug. Returns cover image URLs when
    available (useful for pattern variants like Master Ball whose PC page
    has a photo that differs from the generic TCG API scan).
    """
    urls = _candidate_urls(name, set_name, card_number, language, variant=variant)
    if not urls:
        return None
    prices_table = await _fetch_prices_with_cache(urls)
    if not prices_table:
        return None
    table, url = prices_table
    image_url, image_url_large = _cover_image_urls(_image_cache_get(url))
    return PriceChartingResult(
        url=url, grade_label="Ungraded",
        price_usd=table.get("Ungraded"),
        all_prices=table, cached=False,
        image_url=image_url, image_url_large=image_url_large,
    )


async def fetch_chart_history(name: str, set_name: str, card_number: str,
                               language: str = "english",
                               variant: Optional[str] = None,
                               series: str = "used") -> Optional[tuple[list[tuple[int, float]], str]]:
    """Scrape PriceCharting's embedded `VGPC.chart_data` for this card.

    Returns `([(timestamp_ms, price_usd), ...] sorted ascending, url)`, or
    None. `series` selects which PriceCharting line to read — "used" is the
    raw/ungraded line (see `_CHART_DATA_RE` for the verified mapping).

    Bypasses the price-table cache: this hits the same product page but reads
    a different embedded payload, and the result (a multi-year series) is
    meant to be persisted into `price_history` rather than cached here.
    """
    urls = _candidate_urls(name, set_name, card_number, language, variant=variant)
    if not urls:
        return None
    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting chart fetch failed %s: %s", url, e)
                continue
            if r.status_code != 200:
                continue
            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                continue
            cleaned = _parse_chart_series(r.text, series)
            if not cleaned:
                continue
            return cleaned, url
    return None


async def fetch_sealed_chart_history(
    name: str,
    set_name: str,
    product_type: str,
    language: str = "english",
    series: str = "used",
) -> Optional[tuple[list[tuple[int, float]], str]]:
    """Like `fetch_chart_history`, but for sealed products (booster boxes,
    ETBs, etc.) — reads the same `VGPC.chart_data["used"]` series from the
    sealed-product page that `lookup_sealed_price` fetches for pricing.

    Returns None if `product_type` isn't in SEALED_PRODUCT_SLUGS, the
    product isn't in PriceCharting's catalogue, or chart_data has no
    nonzero points for `series`.
    """
    urls = _sealed_product_urls(name, set_name, product_type, language)
    if not urls:
        return None

    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting sealed chart fetch failed %s: %s", url, e)
                continue

            if r.status_code != 200:
                continue

            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                continue

            cleaned = _parse_chart_series(r.text, series)
            if cleaned:
                return cleaned, url

    return None


async def _fetch_prices_with_cache(urls: list[str]) -> Optional[tuple[dict, str]]:
    """Walk URL candidates; return (price_table, url) for the first hit.
    Cache hits return immediately; cache misses fetch, cache prices, and cache
    the cover image so `lookup_raw_price` can return it without a second fetch."""
    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            cached = _cache_get(url)
            if cached is not None:
                # Price cached but image may not have been cached on earlier
                # fetches (before image caching was added). One-time re-fetch
                # to populate the image cache; after that _image_cache_has
                # returns True and this branch is skipped.
                if not _image_cache_has(url):
                    try:
                        r = await client.get(url, follow_redirects=True)
                        if r.status_code == 200:
                            _image_cache_set(url, _parse_cover_image_base(r.text))
                        else:
                            _image_cache_set(url, None)
                    except Exception:
                        _image_cache_set(url, None)
                return cached, url
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting fetch failed %s: %s", url, e)
                continue
            if r.status_code != 200:
                continue
            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                continue
            prices = _parse_price_table(r.text)
            if not prices:
                continue
            _cache_set(url, prices)
            # Also cache the cover image (reuse the sealed-image cache table —
            # same schema, keyed by URL). Cards have cover art too; having it
            # lets lookup_raw_price return the PC photo for pattern variants
            # (e.g. Master Ball Umbreon) where the TCG API image is generic.
            if _image_cache_get(url) is None:
                _image_cache_set(url, _parse_cover_image_base(r.text))
            return prices, url
    return None


async def lookup_graded_price(name: str, set_name: str, card_number: str,
                               language: str, grade_company: str,
                               grade: float,
                               variant: Optional[str] = None) -> Optional[PriceChartingResult]:
    """Return a PriceChartingResult for the requested grade, or None if the
    card / grade isn't in PriceCharting's catalogue."""
    row_label = GRADE_TO_PC_ROW.get((grade_company.upper(), float(grade)))
    if not row_label:
        return None

    urls = _candidate_urls(name, set_name, card_number, language, variant=variant)
    if not urls:
        return None

    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            # Cache hit?
            cached = _cache_get(url)
            if cached is not None:
                price = cached.get(row_label)
                return PriceChartingResult(
                    url=url, grade_label=row_label,
                    price_usd=price, all_prices=cached, cached=True,
                )
            # Miss — fetch fresh
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting fetch failed %s: %s", url, e)
                continue
            if r.status_code == 404:
                continue
            if r.status_code != 200:
                log.warning("PriceCharting returned %s for %s", r.status_code, url)
                continue

            # Card-not-found behaviour: PC silently 200-redirects to a
            # search-results page rather than returning a real 404.
            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                log.info("PriceCharting redirected %s → %s (card not in catalogue)",
                         url, r.url)
                continue

            prices = _parse_price_table(r.text)
            if not prices:
                continue
            _cache_set(url, prices)
            return PriceChartingResult(
                url=url, grade_label=row_label,
                price_usd=prices.get(row_label),
                all_prices=prices, cached=False,
            )

    return None


async def lookup_sealed_price(
    name: str,
    set_name: str,
    product_type: str,
    language: str = "english",
) -> "PriceChartingResult | None":
    """Return a PriceChartingResult for a sealed product (booster box, ETB, etc.).

    Builds a PriceCharting URL from the set slug and the product-type slug,
    checks the 24h cache, fetches if needed, and parses the price table.
    Looks for a "Sealed" row first; falls back to "Ungraded" if absent.

    Returns None if `product_type` is not in SEALED_PRODUCT_SLUGS or if none
    of the candidate product pages are in PriceCharting's catalogue.
    """
    urls = _sealed_product_urls(name, set_name, product_type, language)
    if not urls:
        return None

    async with httpx.AsyncClient(timeout=15.0,
                                  headers={"User-Agent": USER_AGENT}) as client:
        for url in urls:
            # Cache hit?
            cached = _cache_get(url)
            if cached is not None:
                price_label = "Sealed" if "Sealed" in cached else "Ungraded"
                image_url, image_url_large = _cover_image_urls(_image_cache_get(url))
                return PriceChartingResult(
                    url=url,
                    grade_label=price_label,
                    price_usd=cached.get(price_label),
                    all_prices=cached,
                    cached=True,
                    image_url=image_url,
                    image_url_large=image_url_large,
                )

            # Cache miss — fetch
            try:
                r = await client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                log.warning("PriceCharting sealed fetch failed %s: %s", url, e)
                continue

            if r.status_code != 200:
                log.warning("PriceCharting sealed returned %s for %s", r.status_code, url)
                continue

            final_path = str(r.url.path)
            if final_path.startswith("/search-products") or final_path == "/":
                log.info("PriceCharting sealed redirected %s → %s (not in catalogue)", url, r.url)
                continue

            prices = _parse_price_table(r.text)
            if not prices:
                continue

            _cache_set(url, prices)
            image_base = _parse_cover_image_base(r.text)
            _image_cache_set(url, image_base)
            image_url, image_url_large = _cover_image_urls(image_base)

            price_label = "Sealed" if "Sealed" in prices else "Ungraded"
            return PriceChartingResult(
                url=url,
                grade_label=price_label,
                price_usd=prices.get(price_label),
                all_prices=prices,
                cached=False,
                image_url=image_url,
                image_url_large=image_url_large,
            )

    return None


async def search_products(query: str, limit: int = 10) -> list[PriceChartingSearchResult]:
    """Free-text search of PriceCharting's catalogue — last-resort card
    identity when neither Pokemon TCG API (EN-only) nor TCGdex (which has
    REGISTERED but not POPULATED some Chinese-exclusive sets, e.g. CSV4C)
    can find a match. PriceCharting indexes these sets directly, including
    a cover-art thumbnail per card.
    """
    q = (query or "").strip()
    if not q:
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0,
                                      headers={"User-Agent": USER_AGENT}) as client:
            r = await client.get(f"{PC_BASE}/search-products",
                                  params={"type": "prices", "q": q})
    except httpx.HTTPError as e:
        log.warning("PriceCharting search failed for %r: %s", q, e)
        return []
    if r.status_code != 200:
        return []
    return _parse_search_results(r.text, limit=limit)
