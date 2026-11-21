const DORK_PATTERNS = {
    "Stripe": [
        '"powered by stripe" OR "checkout.stripe.com" -site:stripe.com -site:github.com -site:dashboard',
        'inurl:stripe intitle:checkout -inurl:(admin OR login OR dashboard)'
    ],
    "PayPal": [
        '"paypal checkout" OR "pay with paypal" OR "paypal express" -site:paypal.com',
        '"paypal checkout" OR "pay with paypal" -site:paypal.com'
    ],
    "Square": [
        '"powered by square" OR "square online" checkout'
    ],
    "Shopify": [
        '"powered by shopify" OR ".myshopify.com" -inurl:admin',
        'inurl:.myshopify.com intitle:store'
    ],
    "WooCommerce": [
        '("woocommerce" OR "wc-") (checkout OR cart) ("powered by" OR "/wc-")'
    ],
    "Magento": [
        'mage- OR magento ("checkout" OR "cart") -site:github.com'
    ],
    "BigCommerce": [
        'bigcommerce OR stencil ("checkout" OR store) -site:github.com'
    ],
    "Books": [
        '(bookstore OR "online bookstore" OR "book shop") ("add to cart" OR checkout) -amazon -ebay'
    ],
    "Shoes": [
        '("running shoes" OR sneakers OR footwear) ("buy now" OR "add to cart" OR checkout) -amazon -ebay'
    ],
    "Gift Cards": [
        '("egift card" OR "electronic gift card" OR giftcard) (buy OR purchase OR store) -amazon'
    ]
};

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

let currentDetections = [];

async function getCurrentTab() {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    return tabs[0];
}

function detectTechnologies(html) {
    if (!html || html.length < 50) return [];
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

async function loadDetections() {
    const statusEl = document.getElementById('status');
    const techListEl = document.getElementById('tech-list');
    const detectionsView = document.getElementById('view-detections');
    const dorksView = document.getElementById('view-dorks');
    
    dorksView.style.display = 'none';
    detectionsView.style.display = 'block';
    
    const tab = await getCurrentTab();
    
    if (!tab || !tab.id || tab.url.startsWith('chrome://') || tab.url.startsWith('chrome-extension://')) {
        statusEl.textContent = 'Not available on browser internal pages.';
        techListEl.innerHTML = '';
        return;
    }
    
    statusEl.textContent = 'Detecting technologies...';
    techListEl.innerHTML = '';
    
    try {
        const response = await chrome.tabs.sendMessage(tab.id, { action: "getDetections" });
        let techs = [];
        
        if (response && response.technologies) {
            techs = response.technologies;
        } else {
            // fallback: try to get page HTML directly (requires scripting permission)
            const results = await chrome.scripting.executeScript({
                target: { tabId: tab.id },
                func: () => document.documentElement.outerHTML
            });
            if (results && results[0] && results[0].result) {
                techs = detectTechnologies(results[0].result);
            }
        }
        
        currentDetections = techs;
        
        if (techs.length === 0) {
            statusEl.innerHTML = `
                No matching technologies detected.<br>
                <span style="font-size:12px;">Try visiting a store that uses Shopify, Stripe, WooCommerce etc.</span>
            `;
            return;
        }
        
        statusEl.textContent = `Detected ${techs.length} technology(ies):`;
        
        techs.forEach(tech => {
            const div = document.createElement('div');
            div.className = 'tech-item';
            div.innerHTML = `
                <span class="tech-name">${tech}</span>
                <button class="btn" onclick="showDorks('${tech}')">Suggest dorks →</button>
            `;
            techListEl.appendChild(div);
        });
    } catch (err) {
        console.error(err);
        statusEl.textContent = 'Could not scan page. Refresh and try again.';
    }
}

window.showDorks = async function(tech) {
    const detectionsView = document.getElementById('view-detections');
    const dorksView = document.getElementById('view-dorks');
    const currentTechEl = document.getElementById('current-tech');
    const dorksListEl = document.getElementById('dorks-list');
    
    detectionsView.style.display = 'none';
    dorksView.style.display = 'block';
    
    currentTechEl.textContent = tech;
    dorksListEl.innerHTML = '';
    
    let patterns = DORK_PATTERNS[tech] || [];
    
    // Try to fetch fresh patterns from local Dork Scout server
    try {
        const resp = await fetch(`http://127.0.0.1:8000/api/dorks?tech=${encodeURIComponent(tech)}`, {
            mode: 'no-cors'
        });
        // Note: due to no-cors we cannot read response, but the attempt warms the server
        // In real usage the extension would use a background service worker with host permissions.
        // For this implementation we rely on the bundled patterns.
    } catch (e) {
        // Server not running or blocked - use bundled data (expected)
    }
    
    if (patterns.length === 0) {
        patterns = [`"powered by ${tech}" OR inurl:${tech.toLowerCase()} (checkout OR store)`];
    }
    
    patterns.forEach((signature, index) => {
        const div = document.createElement('div');
        div.className = 'dork-item';
        div.innerHTML = `
            <div class="dork-signature">${signature}</div>
            <button class="btn" onclick="runDork('${encodeURIComponent(signature)}')">Search on Google</button>
        `;
        dorksListEl.appendChild(div);
    });
};

window.runDork = function(encodedSignature) {
    const query = decodeURIComponent(encodedSignature);
    const url = `https://www.google.com/search?q=${query}`;
    window.open(url, '_blank');
};

window.goBack = function() {
    const detectionsView = document.getElementById('view-detections');
    const dorksView = document.getElementById('view-dorks');
    detectionsView.style.display = 'block';
    dorksView.style.display = 'none';
};

window.openDorkScout = function() {
    window.open('http://127.0.0.1:8000', '_blank');
};

// Initialize popup
document.addEventListener('DOMContentLoaded', () => {
    loadDetections();
});
