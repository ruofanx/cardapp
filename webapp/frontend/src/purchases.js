import { Purchases, LOG_LEVEL, PURCHASES_ERROR_CODE } from '@revenuecat/purchases-capacitor'

// Public (safe to embed client-side) RevenueCat SDK key for the iOS app.
// Placeholder until the RevenueCat project exists — see
// docs/superpowers/specs/2026-07-19-revenuecat-iap-design.md §7.
const REVENUECAT_PUBLIC_SDK_KEY = 'appl_REPLACE_ME'
const ENTITLEMENT_ID = 'pro'
const INTRO_ELIGIBILITY_STATUS_ELIGIBLE = 2

let configured = false

export async function configurePurchases(accountId) {
  if (configured || !accountId) return
  await Purchases.setLogLevel({ level: LOG_LEVEL.INFO })
  await Purchases.configure({ apiKey: REVENUECAT_PUBLIC_SDK_KEY, appUserID: accountId })
  configured = true
}

export async function logOutPurchases() {
  if (!configured) return
  try { await Purchases.logOut() } catch { /* already logged out / never configured */ }
  configured = false
}

export async function getProPackage() {
  const offerings = await Purchases.getOfferings()
  return offerings.current?.availablePackages?.[0] || null
}

export async function isEligibleForTrial(pkg) {
  if (!pkg?.product?.identifier) return false
  const result = await Purchases.checkTrialOrIntroductoryPriceEligibility({
    productIdentifiers: [pkg.product.identifier],
  })
  return result?.[pkg.product.identifier]?.status === INTRO_ELIGIBILITY_STATUS_ELIGIBLE
}

export async function purchasePackage(pkg) {
  const { customerInfo } = await Purchases.purchasePackage({ aPackage: pkg })
  return customerInfo
}

export function purchaseWasCancelled(err) {
  return err?.code === PURCHASES_ERROR_CODE.PURCHASE_CANCELLED_ERROR
}

export function purchaseIsPending(err) {
  return err?.code === PURCHASES_ERROR_CODE.PAYMENT_PENDING_ERROR
}

export async function restorePurchases() {
  const { customerInfo } = await Purchases.restorePurchases()
  return customerInfo
}

export function customerInfoIsPro(customerInfo) {
  return Boolean(customerInfo?.entitlements?.active?.[ENTITLEMENT_ID])
}
