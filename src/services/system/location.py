import requests
import logging
import os

# Cache for IP Location
_ip_location_cache = None
_ip_country_code_cache = None

# Country code -> language code for search locale (e.g. PL -> pl, US -> en)
_COUNTRY_TO_LANG = {
    'US': 'en', 'GB': 'en', 'AU': 'en', 'CA': 'en', 'NZ': 'en', 'IE': 'en',
    'PL': 'pl', 'DE': 'de', 'FR': 'fr', 'ES': 'es', 'IT': 'it', 'PT': 'pt',
    'NL': 'nl', 'BE': 'nl', 'RU': 'ru', 'JP': 'ja', 'CN': 'zh', 'IN': 'en',
    'BR': 'pt', 'MX': 'es', 'AR': 'es', 'KR': 'ko', 'SE': 'sv', 'NO': 'no',
    'FI': 'fi', 'DK': 'da', 'TR': 'tr', 'GR': 'el', 'IL': 'he', 'UA': 'uk',
    'CZ': 'cs', 'HU': 'hu', 'RO': 'ro', 'CH': 'de', 'AT': 'de'
}

def get_ip_location():
    global _ip_location_cache, _ip_country_code_cache
    if _ip_location_cache: return _ip_location_cache

    try:
        resp = requests.get("http://ip-api.com/json/?fields=status,country,countryCode,city,regionName", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') == 'success':
                loc_str = f"{data.get('city')}, {data.get('regionName')}, {data.get('country')}"
                _ip_location_cache = loc_str
                _ip_country_code_cache = data.get('countryCode')  # e.g. PL, US
                logging.info(f"IP Location: {loc_str} (countryCode: {_ip_country_code_cache})")
                return loc_str
    except Exception as e:
        logging.error(f"IP Loc Failed: {e}")
    
    return "Unknown Location"

def get_search_locale():
    """Locale for search (e.g. pl-PL, en-US). Prefer user's location: system locale, then IP-based."""
    # 1. Try system locale (user's OS language)
    loc = get_system_location()
    if loc and loc != "en-US":
        return loc
    # 2. If system is en-US, use IP country so e.g. user in Poland gets pl-PL
    global _ip_country_code_cache
    if _ip_country_code_cache is None:
        get_ip_location()  # populate cache
    if _ip_country_code_cache:
        lang = _COUNTRY_TO_LANG.get(_ip_country_code_cache.upper(), 'en')
        return f"{lang}-{_ip_country_code_cache.upper()}"
    return loc or "en-US"

def get_system_location():
    if os.name == 'nt':
        try:
            import locale
            lang_code, _ = locale.getdefaultlocale()
            if lang_code:
                return lang_code.replace('_', '-')
        except: pass
        return "en-US"

    try:
        # 1. Get System Timezone
        if os.path.exists("/etc/timezone"):
            with open("/etc/timezone", "r") as f:
                sys_tz = f.read().strip()
        else:
            return "en-US"

        # 2. Map Timezone -> Country Code using zone1970.tab
        country_code = "US"
        zone_tab = "/usr/share/zoneinfo/zone1970.tab"
        if os.path.exists(zone_tab):
            try:
                with open(zone_tab, 'r') as f:
                    for line in f:
                        if line.startswith('#'): continue
                        parts = line.split('\t')
                        if len(parts) >= 3:
                            codes = parts[0].split(',')
                            tz_name = parts[2].strip()
                            if tz_name == sys_tz:
                                country_code = codes[0]
                                break
            except: pass

        # 3. Map Country Code -> Language Code (Common mappings)
        # Fallback to 'en' if unknown
        lang_map = {
            'US': 'en', 'GB': 'en', 'AU': 'en', 'CA': 'en', 'NZ': 'en', 'IE': 'en',
            'PL': 'pl', 'DE': 'de', 'FR': 'fr', 'ES': 'es', 'IT': 'it', 'PT': 'pt',
            'NL': 'nl', 'BE': 'nl', 'RU': 'ru', 'jp': 'ja', 'JP': 'ja', 'CN': 'zh',
            'IN': 'en', 'BR': 'pt', 'MX': 'es', 'AR': 'es', 'KR': 'ko', 'SE': 'sv',
            'NO': 'no', 'FI': 'fi', 'DK': 'da', 'TR': 'tr', 'GR': 'el', 'IL': 'he',
            'UA': 'uk', 'CZ': 'cs', 'HU': 'hu', 'RO': 'ro', 'CH': 'de', 'AT': 'de'
        }
        lang = lang_map.get(country_code, 'en')

        # 4. Construct Locale
        return f"{lang}-{country_code}"

    except Exception as e:
        logging.error(f"Loc Check Failed: {e}")
        return "en-US"
