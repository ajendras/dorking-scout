const TECH_SIGNATURES = {
    "Shopify": [
        /powered by shopify/i,
        /cdn\.shopify\.com/i,
        /Shopify\.Checkout/i,
        /"shopify":/i,
        /shopify\.theme/i,
        /\/cart\/add.*shopify/i
    ],
    "Stripe": [
        /js\.stripe\.com/i,
        /checkout\.stripe\.com/i,
        /Stripe\(/i,
        /data-stripe/i,
        /stripe\.js/i,
        /"stripe":\s*\{/i
    ],
    "PayPal": [
        /paypalobjects\.com/i,
        /PayPal\.Buttons/i,
        /paypal\.com\/sdk/i,
        /btn_paypal/i,
        /paypal-express/i
    ],
    "Square": [
        /squareup\.com/i,
        /sqpaymentform/i,
        /square\.site/i,
        /commerce\.square/i
    ],
    "WooCommerce": [
        /woocommerce/i,
        /wc-/i,
        /\/wp-content\/plugins\/woocommerce/i,
        /var wc_add_to_cart_params/i,
        /woocommerce-checkout/i
    ],
    "Magento": [
        /mage-/i,
        /magento/i,
        /mage\.js/i,
        /static\/version/i
    ],
    "BigCommerce": [
        /bigcommerce/i,
        /stencil/i,
        /bcapp/i
    ]
};

function detectTechnologies(html) {
    if (!html || typeof html !== "string" || html.length < 50) {
        return [];
    }
    const htmlLower = html.toLowerCase();
    const detected = new Set();
    
    for (const [tech, patterns] of Object.entries(TECH_SIGNATURES)) {
        for (const pattern of patterns) {
            if (pattern.test(htmlLower)) {
                detected.add(tech);
                break;
            }
        }
    }
    return Array.from(detected).sort();
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.action === "getDetections") {
        // Use the full document HTML for detection (same approach as Python detector)
        const html = document.documentElement.outerHTML;
        const technologies = detectTechnologies(html);
        sendResponse({ technologies: technologies });
    }
    // Return true to indicate we will respond asynchronously
    return true;
});
