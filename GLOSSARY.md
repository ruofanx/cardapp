# CardApp

A Pokemon card collection tracker: people log the cards and sealed products they own, see what they are worth, and trade.

## Language

### People

**Account**:
A login identity that owns one or more Profiles.
_Avoid_: User, login

**Profile**:
One person's collection, with its own cards, tags and trades. Older code and routes call this a "user" (`user_id` is a Profile id).
_Avoid_: User

### Collection

**Card**:
One owned item in a Profile's collection: a single card, a graded card, or a sealed product.
_Avoid_: Item, holding

**Tag**:
A named label a Profile attaches to Cards. A trade tag marks Cards available for trade.
_Avoid_: List, folder

**Card store**:
Where a Profile's Cards, Tags, price history and portfolio values are kept.
_Avoid_: Database, persistence layer

### Valuation

**Market price**:
What a Card is worth now according to observed listings and sales, stored on the Card as `current_market_price`.
_Avoid_: Value, estimated price, usd

**Fair value**:
What the pricing model predicts a raw Card should be worth, from its rarity, character, era and release date. Compared against Market price, never the same thing.
_Avoid_: Price, estimate

**Quote**:
A single priced answer for a Card at a given condition or grade, with the source it came from.
_Avoid_: Estimate, lookup
