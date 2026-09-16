import json
import os
import random
import re
import time
import hashlib
import urllib.parse
import base64
import requests
from datetime import datetime, timezone
from PIL import Image
from shapely.geometry import shape, mapping
from shapely.ops import unary_union

# Mapbox Access Token - read from environment, never hardcoded, since this
# script now runs in a deployed container. Locally: `export MAPBOX_ACCESS_TOKEN=...`
# before running. On Cloud Run: set as an env var or (better) a Secret Manager
# secret mounted as an env var in the service config.
# NOTE: rotate the token that was pasted into chat/shared code earlier in
# testing - it's been exposed in plaintext and should be treated as burned.
MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN", "")

HEADERS = {
    "User-Agent": "(MySevereAlertAppTest, tempatkavi@example.com)",
    "Accept": "application/geo+json",
}

# --- SAFE FACEBOOK ASPECT RATIOS ---
ASPECT_RATIOS = {
    "portrait": (1080, 1280),   # Mobile Feed (4:5)
    "square": (1080, 1080),     # Feed Square (1:1)
    "landscape": (1200, 630),   # Wide Preview (1.91:1)
}

# --- OFFICIAL NWS WWA COLOR TABLE ---
# Source: weather.gov's published Watch/Warning/Advisory color chart
# (the same table used on NWS hazard maps). Halos are computed automatically
# below rather than hand-picked per entry, since maintaining ~100 manual
# halo choices isn't worth it - contrast is what matters, not the exact hue.
NWS_COLOR_PALETTE = {
    "Tsunami Warning": "#FD6347",
    "Tornado Warning": "#FF0000",
    "Extreme Wind Warning": "#FF8C00",
    "Severe Thunderstorm Warning": "#FFA500",
    "Flash Flood Warning": "#8B0000",
    "Flash Flood Statement": "#8B0000",
    "Severe Weather Statement": "#00FFFF",
    "Shelter In Place Warning": "#FA8072",
    "Evacuation Immediate": "#7FFF00",
    "Civil Danger Warning": "#FFB6C1",
    "Nuclear Power Plant Warning": "#4B0082",
    "Radiological Hazard Warning": "#4B0082",
    "Hazardous Materials Warning": "#4B0082",
    "Fire Warning": "#A0522D",
    "Civil Emergency Message": "#FFB6C1",
    "Law Enforcement Warning": "#C0C0C0",
    "Storm Surge Warning": "#B524F7",
    "Hurricane Force Wind Warning": "#CD5C5C",
    "Hurricane Warning": "#DC143C",
    "Typhoon Warning": "#DC143C",
    "Special Marine Warning": "#FFA500",
    "Blizzard Warning": "#FF4500",
    "Snow Squall Warning": "#C71585",
    "Ice Storm Warning": "#8B008B",
    "Heavy Freezing Spray Warning": "#00BFFF",
    "Winter Storm Warning": "#FF69B4",
    "Lake Effect Snow Warning": "#008B8B",
    "Dust Storm Warning": "#FFE4C4",
    "Blowing Dust Warning": "#FFE4C4",
    "High Wind Warning": "#DAA520",
    "Tropical Storm Warning": "#B22222",
    "Storm Warning": "#9400D3",
    "Tsunami Advisory": "#D2691E",
    "Tsunami Watch": "#FF00FF",
    "Avalanche Warning": "#1E90FF",
    "Earthquake Warning": "#8B4513",
    "Volcano Warning": "#2F4F4F",
    "Ashfall Warning": "#A9A9A9",
    "Flood Warning": "#00FF00",
    "Coastal Flood Warning": "#228B22",
    "Lakeshore Flood Warning": "#228B22",
    "Ashfall Advisory": "#696969",
    "High Surf Warning": "#228B22",
    "Excessive Heat Warning": "#C71585",
    "Tornado Watch": "#FFFF00",
    "Severe Thunderstorm Watch": "#DB7093",
    "Flash Flood Watch": "#2E8B57",
    "Gale Warning": "#DDA0DD",
    "Flood Statement": "#00FF00",
    "Extreme Cold Warning": "#0000FF",
    "Freeze Warning": "#483D8B",
    "Red Flag Warning": "#FF1493",
    "Storm Surge Watch": "#DB7FF7",
    "Hurricane Watch": "#FF00FF",
    "Hurricane Force Wind Watch": "#9932CC",
    "Typhoon Watch": "#FF00FF",
    "Tropical Storm Watch": "#F08080",
    "Storm Watch": "#FFE4B5",
    "Tropical Cyclone Local Statement": "#FFE4B5",
    "Winter Weather Advisory": "#7B68EE",
    "Avalanche Advisory": "#CD853F",
    "Cold Weather Advisory": "#AFEEEE",
    "Heat Advisory": "#FF7F50",
    "Flood Advisory": "#00FF7F",
    "Coastal Flood Advisory": "#7CFC00",
    "Lakeshore Flood Advisory": "#7CFC00",
    "High Surf Advisory": "#BA55D3",
    "Dense Fog Advisory": "#708090",
    "Dense Smoke Advisory": "#F0E68C",
    "Small Craft Advisory": "#D8BFD8",
    "Brisk Wind Advisory": "#D8BFD8",
    "Hazardous Seas Warning": "#D8BFD8",
    "Dust Advisory": "#BDB76B",
    "Blowing Dust Advisory": "#BDB76B",
    "Lake Wind Advisory": "#D2B48C",
    "Wind Advisory": "#D2B48C",
    "Frost Advisory": "#6495ED",
    "Freezing Fog Advisory": "#008080",
    "Freezing Spray Advisory": "#00BFFF",
    "Low Water Advisory": "#A52A2A",
    "Local Area Emergency": "#C0C0C0",
    "Winter Storm Watch": "#4682B4",
    "Rip Current Statement": "#40E0D0",
    "Beach Hazards Statement": "#40E0D0",
    "Gale Watch": "#FFC0CB",
    "Avalanche Watch": "#F4A460",
    "Hazardous Seas Watch": "#483D8B",
    "Heavy Freezing Spray Watch": "#BC8F8F",
    "Flood Watch": "#2E8B57",
    "Coastal Flood Watch": "#66CDAA",
    "Lakeshore Flood Watch": "#66CDAA",
    "High Wind Watch": "#B8860B",
    "Excessive Heat Watch": "#800000",
    "Extreme Cold Watch": "#5F9EA0",
    "Freeze Watch": "#00FFFF",
    "Fire Weather Watch": "#FFDEAD",
    "Extreme Fire Danger": "#E9967A",
    "911 Telephone Outage": "#C0C0C0",
    "Coastal Flood Statement": "#6B8E23",
    "Lakeshore Flood Statement": "#6B8E23",
    "Special Weather Statement": "#FFE4B5",
    "Marine Weather Statement": "#FFDAB9",
    "Air Quality Alert": "#808080",
    "Air Stagnation Advisory": "#808080",
    "Hazardous Weather Outlook": "#EEE8AA",
    "Hydrologic Outlook": "#90EE90",
    "Short Term Forecast": "#98FB98",
    "Administrative Message": "#C0C0C0",
    "Test": "#F0FFFF",
    "Child Abduction Emergency": "#FFFFFF",
    "Blue Alert": "#FFFFFF",
    "Default": "#FF0000",
}


def contrasting_halo(hex_color: str) -> str:
    """Picks a black or white halo based on the primary color's luminance."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if luminance > 0.6 else "#FFFFFF"

# Mapbox overlay hard limit: 2083 chars total URL, 100 features max.
# Keep a safety margin under that for the encoded geojson string itself.
MAX_ENCODED_GEOJSON_CHARS = 1900

# Douglas-Peucker simplification tolerances (in degrees) to try, in increasing
# strength, if the polygon is still too large after dropping the halo layer.
# ~0.001 deg ≈ 100m, ~0.05 deg ≈ 5.5km at these latitudes - the loop stops at
# the first tolerance that fits, so most alerts will only need a light pass.
SIMPLIFY_TOLERANCES = [0.001, 0.003, 0.01, 0.03, 0.08, 0.15]


def format_alert_time(iso_str: str) -> str:
    """
    Formats an NWS ISO8601 timestamp into a readable clock time + UTC offset,
    e.g. '8:00 PM (UTC-4)'. NWS timestamps carry only a numeric offset, not a
    zone abbreviation (EDT vs EST is ambiguous from the offset alone - e.g.
    -04:00 could be EDT or AST), so we show the offset rather than guess.
    Used as a fallback when we can't determine which US state an alert
    belongs to (see format_alert_time_local, which is what you actually want
    for a US audience).
    """
    dt = datetime.fromisoformat(iso_str)
    offset_hours = int(dt.utcoffset().total_seconds() // 3600)
    time_str = dt.strftime("%-I:%M %p")
    return f"{time_str} (UTC{offset_hours:+d})"


# Primary IANA timezone per state - used to resolve the correct, DST-aware
# zone abbreviation (EDT/CDT/MDT/PDT/etc.) for display. This is a
# one-zone-per-state approximation: a few states technically span two zones
# (e.g. the TX/FL panhandles are partly Central, west TX touches Mountain),
# but for a social post this is the right level of precision - nobody reads
# "EDT" vs "CDT" as a promise of county-level exactness.
STATE_TIMEZONES = {
    "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix",
    "AR": "America/Chicago", "CA": "America/Los_Angeles", "CO": "America/Denver",
    "CT": "America/New_York", "DE": "America/New_York", "DC": "America/New_York",
    "FL": "America/New_York", "GA": "America/New_York", "HI": "Pacific/Honolulu",
    "ID": "America/Boise", "IL": "America/Chicago", "IN": "America/Indiana/Indianapolis",
    "IA": "America/Chicago", "KS": "America/Chicago", "KY": "America/New_York",
    "LA": "America/Chicago", "ME": "America/New_York", "MD": "America/New_York",
    "MA": "America/New_York", "MI": "America/Detroit", "MN": "America/Chicago",
    "MS": "America/Chicago", "MO": "America/Chicago", "MT": "America/Denver",
    "NE": "America/Chicago", "NV": "America/Los_Angeles", "NH": "America/New_York",
    "NJ": "America/New_York", "NM": "America/Denver", "NY": "America/New_York",
    "NC": "America/New_York", "ND": "America/Chicago", "OH": "America/New_York",
    "OK": "America/Chicago", "OR": "America/Los_Angeles", "PA": "America/New_York",
    "RI": "America/New_York", "SC": "America/New_York", "SD": "America/Chicago",
    "TN": "America/Chicago", "TX": "America/Chicago", "UT": "America/Denver",
    "VT": "America/New_York", "VA": "America/New_York", "WA": "America/Los_Angeles",
    "WV": "America/New_York", "WI": "America/Chicago", "WY": "America/Denver",
    "PR": "America/Puerto_Rico", "VI": "America/Puerto_Rico",
    "GU": "Pacific/Guam", "AS": "Pacific/Pago_Pago",
}


def get_alert_state(feature: dict) -> str:
    """
    Best-effort US state for an alert, from its UGC zone/county codes
    (e.g. 'FLZ141' -> 'FL'). Used purely to pick the right local timezone
    for display - not for anything geometry-related.
    """
    ugc_codes = feature.get("properties", {}).get("geocode", {}).get("UGC", [])
    if ugc_codes:
        return ugc_codes[0][:2].upper()
    return ""


def format_alert_time_local(iso_str: str, state: str) -> str:
    """
    Formats an NWS timestamp as the LOCAL clock time + zone abbreviation a
    US audience actually recognizes, e.g. '8:00 PM EDT' - not a raw UTC
    offset. The offset alone is ambiguous (-04:00 is EDT most of the year,
    but AST in Puerto Rico/the Virgin Islands with no DST), so this uses the
    alert's state to resolve the correct IANA timezone, which correctly
    handles DST for whatever date the alert falls on. Falls back to the
    UTC-offset format if the state can't be mapped.
    """
    from zoneinfo import ZoneInfo

    tz_name = STATE_TIMEZONES.get(state)
    if not tz_name:
        return format_alert_time(iso_str)

    try:
        dt_utc = datetime.fromisoformat(iso_str)
        local_dt = dt_utc.astimezone(ZoneInfo(tz_name))
        time_str = local_dt.strftime("%-I:%M %p")
        zone_abbr = local_dt.strftime("%Z")
        return f"{time_str} {zone_abbr}"
    except Exception:
        return format_alert_time(iso_str)


def parse_description_bullets(description: str) -> list:
    """Splits an NWS description's '* WHAT...', '* WHERE...' style bullets into a clean list."""
    if not description:
        return []
    bullets = []
    for chunk in description.split("*"):
        line = " ".join(chunk.strip().split())
        if line:
            bullets.append(line)
    return bullets


def clean_bullet(bullet: str) -> str:
    """Turns 'WHAT...Dangerous rip currents.' into 'What: Dangerous rip currents.'"""
    label, _, rest = bullet.partition("...")
    return f"{label.strip().title()}: {rest.strip()}"


def build_plain_english_opener(feature: dict, bullets: list) -> str:
    """
    A synthesized, human-sounding opening line instead of leading with a
    raw WHAT/WHERE/WHEN bullet dump - e.g. 'Dangerous rip currents for
    Coastal Volusia — through early this evening' rather than three
    separately parsed fragments read as fragments. Falls back to the NWS
    headline (still human-written, just less tailored) if a usable WHAT
    bullet isn't found.
    """
    props = feature.get("properties", {})
    event = props.get("event", "Weather Alert")
    area_desc = props.get("areaDesc", "")
    headline = props.get("headline", "")
    zone_text = shorten_area_desc(area_desc)

    what_text, when_text = "", ""
    for b in bullets:
        lower = b.lower()
        if lower.startswith("what:"):
            what_text = b.split(":", 1)[1].strip().rstrip(".")
        elif lower.startswith("when:"):
            when_text = b.split(":", 1)[1].strip().rstrip(".")

    if what_text:
        sentence = f"{what_text} for {zone_text}"
        if when_text:
            sentence += f" — {when_text}"
        return sentence + "."
    return headline or f"{event} in effect for {zone_text}."


def build_hashtags(feature: dict) -> str:
    """A couple of lightweight hashtags (state + event) for discoverability."""
    props = feature.get("properties", {})
    event = props.get("event", "")
    state = get_alert_state(feature)
    event_tag = re.sub(r"[^A-Za-z]", "", event)
    tags = []
    if state:
        tags.append(f"#{state}Weather")
    if event_tag:
        tags.append(f"#{event_tag}")
    return " ".join(tags)


def build_facebook_caption(feature: dict, is_update: bool = False) -> str:
    """
    Builds a ready-to-post caption: icon + event header, a synthesized
    plain-English opener, the FULL safety instruction (kept intact and
    prominent - this is the one part of the raw NWS text that should never
    be trimmed), the WHAT/WHERE/WHEN/IMPACTS bullets (with any "Additional
    Details" bullet demoted to after the core ones, since it's usually
    about adjacent areas rather than the alert itself), expiry, source, and
    a couple of hashtags.

    is_update=True adds a line making clear this is the SAME warning being
    re-posted because something about it genuinely changed (extended,
    expanded, corrected) - not a brand-new alert.
    """
    props = feature.get("properties", {})
    event = props.get("event", "Weather Alert")
    expires = props.get("expires", "")
    instruction = props.get("instruction", "")
    sender = props.get("senderName", "")
    web = props.get("web", "")
    description = props.get("description", "")

    bullets = [clean_bullet(b) for b in parse_description_bullets(description)]
    main_bullets = [b for b in bullets if not b.lower().startswith("additional details")]
    extra_bullets = [b for b in bullets if b.lower().startswith("additional details")]

    opener = build_plain_english_opener(feature, bullets)
    expires_str = format_alert_time_local(expires, get_alert_state(feature)) if expires else ""
    instruction_clean = " ".join(instruction.split()) if instruction else ""
    icon = get_event_icon(event)

    header = f"🔄 UPDATE: {icon} {event.upper()}" if is_update else f"{icon} {event.upper()}"
    lines = [header, "", opener, ""]

    if is_update:
        lines += ["This warning has been updated (extended, expanded, or corrected).", ""]

    if instruction_clean:
        lines += [f"🛟 {instruction_clean}", ""]

    if main_bullets:
        lines += [f"• {b}" for b in main_bullets]
        lines.append("")

    if extra_bullets:
        lines += [f"• {b}" for b in extra_bullets]
        lines.append("")

    if expires_str:
        lines.append(f"⏰ In effect until {expires_str}.")
    if sender:
        lines.append(f"Source: {sender}" + (f" | {web}" if web else ""))

    hashtags = build_hashtags(feature)
    if hashtags:
        lines += ["", hashtags]

    return "\n".join(lines)


def fetch_random_polygon_alert():
    """
    Fetch active alerts from NWS API and return a random one that's usable -
    either it has an inline polygon, or it has UGC zone codes we can resolve
    to a shape via the zones API (see geometry_from_zones).
    """
    url = "https://api.weather.gov/alerts/active"
    print("Fetching active alerts from NWS API...")

    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()
    data = response.json()

    features = data.get("features", [])
    usable_alerts = [
        f for f in features
        if f.get("properties", {}).get("status") != "Test"
        and (
            f.get("geometry") is not None
            or f.get("properties", {}).get("geocode", {}).get("UGC")
        )
    ]

    if not usable_alerts:
        print("No active alerts with usable geometry found right now.")
        return None

    return random.choice(usable_alerts)


def get_nws_colors(event_name: str) -> dict:
    """
    Matches an NWS alert's event name to its official color, preferring the
    most specific match - e.g. "Coastal Flood Warning" over the more generic
    "Flood Warning", since the generic one is also a substring of it.
    """
    event_lower = event_name.lower()

    for alert_key, color in NWS_COLOR_PALETTE.items():
        if alert_key.lower() == event_lower:
            return {"color": color, "halo": contrasting_halo(color)}

    matches = [k for k in NWS_COLOR_PALETTE if k.lower() in event_lower]
    if matches:
        best_key = max(matches, key=len)
        color = NWS_COLOR_PALETTE[best_key]
        return {"color": color, "halo": contrasting_halo(color)}

    color = NWS_COLOR_PALETTE["Default"]
    return {"color": color, "halo": contrasting_halo(color)}


def fetch_zone_geometry(zone_id: str) -> dict:
    """Fetches a single NWS forecast zone's boundary polygon."""
    url = f"https://api.weather.gov/zones/forecast/{zone_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json().get("geometry")
    except requests.RequestException as e:
        print(f"⚠️ Could not fetch zone geometry for {zone_id}: {e}")
        return None


def geometry_from_zones(ugc_codes: list) -> dict:
    """
    Builds a single (Multi)Polygon by fetching and unioning the boundaries of
    one or more NWS forecast zones. This is the fallback for alerts whose own
    `geometry` field is null - very common for zone-based statements and
    advisories (e.g. Rip Current Statement) that target UGC zones rather than
    shipping an inline drawn polygon.
    """
    zone_shapes = []
    for zone_id in ugc_codes:
        geom = fetch_zone_geometry(zone_id)
        if geom:
            zone_shapes.append(shape(geom))

    if not zone_shapes:
        return None

    unioned = unary_union(zone_shapes)
    return mapping(unioned)


def round_coords(geometry: dict, precision: int = 4) -> dict:
    """
    Rounds coordinate precision to shrink the encoded GeoJSON string.
    4 decimal places is ~11m accuracy - plenty for a regional alert map,
    and NWS returns ~6 decimals by default which bloats the URL for no visual benefit.
    """
    def round_ring(ring):
        return [[round(x, precision), round(y, precision)] for x, y in ring]

    if geometry["type"] == "Polygon":
        geometry["coordinates"] = [round_ring(ring) for ring in geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        geometry["coordinates"] = [
            [round_ring(ring) for ring in poly] for poly in geometry["coordinates"]
        ]
    return geometry


def build_encoded_geojson(feature_collection: dict) -> str:
    """Compact-serialize and URL-encode a feature collection."""
    geojson_str = json.dumps(feature_collection, separators=(",", ":"))
    return urllib.parse.quote(geojson_str)


def simplify_geometry(geometry: dict, tolerance: float) -> dict:
    """
    Reduces vertex count with Douglas-Peucker simplification (via shapely).
    preserve_topology=True keeps the polygon valid (no self-intersections)
    even at fairly aggressive tolerances.
    """
    geom = shape(geometry)
    simplified = geom.simplify(tolerance, preserve_topology=True)
    return mapping(simplified)


def build_layers(geometry: dict, primary_color: str, halo_color: str) -> tuple:
    """Builds the halo + main feature dicts for a given geometry."""
    halo_layer = {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "fill": primary_color,
            "fill-opacity": 0.30,
            "stroke": halo_color,
            "stroke-width": 8,
            "stroke-opacity": 1.0,
            "stroke-linejoin": "round",
        },
    }
    main_layer = {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "fill": primary_color,
            "fill-opacity": 0.0,
            "stroke": primary_color,
            "stroke-width": 5,
            "stroke-opacity": 1.0,
            "stroke-linejoin": "round",
        },
    }
    return halo_layer, main_layer


def build_overlay_within_limit(raw_geometry: dict, primary_color: str, halo_color: str) -> str:
    """
    Cascading fallback to guarantee the overlay ALWAYS fits Mapbox's URL limit,
    no matter how complex the source geometry is (e.g. a union of many zones
    along a jagged coastline):
      1. Round to 4-decimal precision, try halo + main.
      2. Drop the halo, try main only.
      3. Progressively simplify the geometry (Douglas-Peucker) and retry main-only
         at increasing tolerance until it fits.
      4. Collapse to the convex hull - always drastically fewer vertices than
         the source shape, regardless of how jagged/multi-part it was.
      5. Collapse to the bounding box (envelope) - a plain rectangle, exactly
         5 coordinate pairs, mathematically guaranteed to fit. This is the
         true last resort so this function can never raise in practice.
    """
    geometry = round_coords(raw_geometry, precision=4)
    halo_layer, main_layer = build_layers(geometry, primary_color, halo_color)

    encoded = build_encoded_geojson({"type": "FeatureCollection", "features": [halo_layer, main_layer]})
    if len(encoded) <= MAX_ENCODED_GEOJSON_CHARS:
        return encoded

    print(f"⚠️ Overlay ({len(encoded)} chars) too large for halo effect — dropping halo.")
    encoded = build_encoded_geojson({"type": "FeatureCollection", "features": [main_layer]})
    if len(encoded) <= MAX_ENCODED_GEOJSON_CHARS:
        return encoded

    print(f"⚠️ Overlay ({len(encoded)} chars) still too large — simplifying polygon.")
    for tolerance in SIMPLIFY_TOLERANCES:
        simplified_geometry = round_coords(simplify_geometry(raw_geometry, tolerance), precision=4)
        _, simplified_main = build_layers(simplified_geometry, primary_color, halo_color)
        encoded = build_encoded_geojson({"type": "FeatureCollection", "features": [simplified_main]})
        if len(encoded) <= MAX_ENCODED_GEOJSON_CHARS:
            print(f"✅ Fit at simplify tolerance {tolerance} ({len(encoded)} chars).")
            return encoded

    print(
        f"⚠️ Overlay ({len(encoded)} chars) still too large even at max simplification "
        f"tolerance ({SIMPLIFY_TOLERANCES[-1]}) — this shape is unusually jagged/complex "
        "(e.g. a many-zone union along a coastline). Falling back to convex hull."
    )
    hull_geometry = round_coords(mapping(shape(raw_geometry).convex_hull), precision=4)
    _, hull_main = build_layers(hull_geometry, primary_color, halo_color)
    encoded = build_encoded_geojson({"type": "FeatureCollection", "features": [hull_main]})
    if len(encoded) <= MAX_ENCODED_GEOJSON_CHARS:
        print(f"✅ Fit using convex hull ({len(encoded)} chars). Shape is now a smoothed outline, not the exact boundary.")
        return encoded

    print(
        f"⚠️ Even the convex hull ({len(encoded)} chars) doesn't fit — this should be extremely rare. "
        "Falling back to a plain bounding-box rectangle over the affected area."
    )
    envelope_geometry = round_coords(mapping(shape(raw_geometry).envelope), precision=4)
    _, envelope_main = build_layers(envelope_geometry, primary_color, halo_color)
    encoded = build_encoded_geojson({"type": "FeatureCollection", "features": [envelope_main]})
    print(f"✅ Fit using bounding box ({len(encoded)} chars).")
    return encoded


def generate_mapbox_nws_image(
    feature: dict,
    token: str,
    output_filename: str = "nws_fb_alert.png",
    map_style: str = "streets-v12",    # Mapbox style
    preset_ratio: str = "portrait",    # 'portrait', 'square', 'landscape'
    padding: int = 140,                # Padding around polygon frame
    label_scale: float = 1.8,          # <--- INCREASES TEXT SIZE! (1.5 to 2.0 works best)
):
    props = feature.get("properties", {})
    event_name = props.get("event", "Default")
    raw_geometry = feature.get("geometry")

    # If the alert has no inline polygon (common for zone-based statements
    # and advisories), build one from its UGC zone codes instead.
    if raw_geometry is None:
        ugc_codes = props.get("geocode", {}).get("UGC", [])
        print(f"ℹ️ '{event_name}' has no inline geometry — resolving shape from {len(ugc_codes)} zone(s): {ugc_codes}")
        raw_geometry = geometry_from_zones(ugc_codes)
        if raw_geometry is None:
            raise ValueError(
                f"'{event_name}' has no geometry and no zone boundaries could be resolved "
                f"from UGC codes {ugc_codes} - cannot generate a map image for this alert."
            )

    # 1. Fetch Official NWS Color Scheme
    palette = get_nws_colors(event_name)
    primary_color = palette["color"]
    halo_color = palette["halo"]

    # 2. Build the overlay, guaranteed to fit Mapbox's URL/size limit -
    # cascades from full halo+main, to main-only, to progressively simplified
    # geometry, so this never hard-fails on a complex polygon mid-pipeline.
    encoded_geojson = build_overlay_within_limit(raw_geometry, primary_color, halo_color)

    # 3. Resolution trick: request a smaller canvas @2x so labels render larger
    # relative to the final output size.
    raw_width, raw_height = ASPECT_RATIOS.get(preset_ratio, (1080, 1280))
    width = int(raw_width / label_scale)
    height = int(raw_height / label_scale)
    scaled_padding = int(padding / label_scale)

    query_params = f"padding={scaled_padding}&access_token={token}"

    static_url = (
        f"https://api.mapbox.com/styles/v1/mapbox/{map_style}/static/"
        f"geojson({encoded_geojson})/"
        f"auto/"
        f"{width}x{height}@2x?{query_params}"
    )

    print(f"Generating image for: '{event_name}' ({primary_color}) with {label_scale}x label scaling...")
    img_resp = requests.get(static_url, timeout=20)

    if img_resp.status_code == 200:
        with open(output_filename, "wb") as f:
            f.write(img_resp.content)

        # Resize to the exact target FB dimensions - the label_scale trick returns
        # a slightly different absolute size (same aspect ratio) since it divides
        # then doubles via @2x, so normalize it here for consistent downstream compositing.
        img = Image.open(output_filename).convert("RGB")
        img = img.resize((raw_width, raw_height), Image.LANCZOS)
        img.save(output_filename)

        print(f"✅ Saved high-visibility map image to '{output_filename}' ({raw_width}x{raw_height})")
    else:
        print(f"❌ Mapbox API Error ({img_resp.status_code}): {img_resp.text}")

    return static_url


def image_to_data_uri(image_path: str) -> str:
    """Reads a local image file and returns it as an embeddable base64 data URI."""
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:image/png;base64,{encoded}"


def shorten_area_desc(area_desc: str, max_parts: int = 2) -> str:
    """
    NWS areaDesc is a semicolon-separated list that can run long
    ('Coastal Volusia; Mainland Northern Brevard; Northern Brevard Barrier
    Islands'). For on-image display, show the first couple and summarize
    the rest - the full list still belongs in the caption.
    """
    parts = [p.strip() for p in area_desc.split(";") if p.strip()]
    if len(parts) <= max_parts:
        return ", ".join(parts)
    shown = ", ".join(parts[:max_parts])
    remaining = len(parts) - max_parts
    return f"{shown} & {remaining} more"


# Emoji icon per event category - a quick visual cue readable in the half-
# second before someone reads the actual headline text. Matched the same
# way as NWS_COLOR_PALETTE: longest/most-specific keyword wins.
EVENT_ICONS = {
    "Tornado": "🌪️", "Hurricane": "🌀", "Tropical Storm": "🌀", "Typhoon": "🌀",
    "Thunderstorm": "⛈️", "Flood": "🌊", "Rip Current": "🌊", "Beach Hazard": "🌊",
    "Surf": "🌊", "Tsunami": "🌊", "Fire": "🔥", "Winter": "❄️", "Snow": "❄️",
    "Blizzard": "❄️", "Ice": "🧊", "Freeze": "🥶", "Frost": "🥶", "Heat": "🌡️",
    "Wind": "💨", "Fog": "🌫️", "Dust": "🌪️", "Smoke": "🌫️",
}


def get_event_icon(event_name: str) -> str:
    """Longest-keyword-match icon lookup, same pattern as get_nws_colors."""
    event_lower = event_name.lower()
    matches = [k for k in EVENT_ICONS if k.lower() in event_lower]
    if matches:
        return EVENT_ICONS[max(matches, key=len)]
    return "⚠️"


def get_action_phrase(severity: str, urgency: str) -> str:
    """
    Plain-language stand-in for raw NWS severity/urgency jargon. 'MODERATE'
    or 'EXPECTED' don't tell a non-meteorologist what to actually do -
    these three tiers map roughly to how urgently a reader should react.
    """
    severity = (severity or "").lower()
    urgency = (urgency or "").lower()
    if severity in ("extreme", "severe") and urgency in ("immediate", "expected"):
        return "TAKE ACTION NOW"
    if severity == "moderate":
        return "STAY ALERT"
    return "BE AWARE"


def get_nearby_place_name(lon: float, lat: float, token: str) -> str:
    """
    Reverse-geocodes a coordinate to the nearest recognizable city/town via
    Mapbox's Geocoding API - 'Near Daytona Beach' means a lot more to a
    scrolling reader than the raw NWS zone name 'Coastal Volusia'. Returns
    "" on any failure so callers can fall back to the zone-based text
    instead of breaking image generation over a non-critical lookup.
    """
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{lon},{lat}.json"
    try:
        resp = requests.get(url, params={"types": "place", "access_token": token}, timeout=10)
        resp.raise_for_status()
        features = resp.json().get("features", [])
        if features:
            return features[0].get("text", "")
    except requests.RequestException:
        pass
    return ""


def build_html_template(
    feature: dict,
    map_image_path: str,
    primary_color: str,
    width: int,
    height: int,
    is_update: bool = False,
) -> str:
    """
    Builds the HTML/CSS for the final post image: the Mapbox render as a
    full-bleed background, with a styled title/severity banner up top and an
    area/countdown/attribution panel at the bottom. Gradient scrims behind
    both keep the text readable regardless of what's under it on the map.
    All text here is real HTML/CSS - crisp at any size, independent of
    Mapbox's own baked-in (and resolution-capped) label rendering.

    is_update=True adds an "UPDATED" tag next to the severity badge, so the
    same critical warning being re-posted (extended/expanded/corrected)
    reads clearly as an update rather than a brand-new alert.
    """
    props = feature.get("properties", {})
    event_raw = props.get("event", "Weather Alert")
    event = event_raw.upper()
    severity = props.get("severity", "")
    urgency = props.get("urgency", "")
    area_desc = props.get("areaDesc", "")
    expires = props.get("expires", "")
    sender = props.get("senderName", "")
    geometry = feature.get("geometry")

    icon = get_event_icon(event_raw)
    action_phrase = get_action_phrase(severity, urgency)

    # "Near <city>" reads far better than a raw NWS zone name to someone
    # scrolling fast - falls back to the zone text if reverse geocoding
    # fails or the alert has no inline geometry to take a centroid from.
    place_name = ""
    if geometry:
        try:
            centroid = shape(geometry).centroid
            place_name = get_nearby_place_name(centroid.x, centroid.y, MAPBOX_ACCESS_TOKEN)
        except Exception:
            place_name = ""
    zone_text = shorten_area_desc(area_desc)
    area_display = f"Near {place_name} — {zone_text}" if place_name else zone_text

    expires_display = format_alert_time_local(expires, get_alert_state(feature)) if expires else ""
    badge_text_color = contrasting_halo(primary_color)
    map_data_uri = image_to_data_uri(map_image_path)

    expires_html = ""
    if expires_display:
        expires_html = f'<div class="expires-text">⏰ Until {expires_display}</div>'

    update_html = ""
    if is_update:
        update_html = '<div class="update-badge">UPDATED</div>'

    return f"""<!DOCTYPE html>
<html>
<head>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{
        width: {width}px;
        height: {height}px;
        font-family: 'Helvetica Neue', Arial, sans-serif;
        position: relative;
        overflow: hidden;
        border: 14px solid {primary_color};
    }}
    .map-bg {{
        position: absolute;
        top: 0; left: 0;
        width: 100%; height: 100%;
        object-fit: cover;
    }}
    .top-banner {{
        position: absolute;
        top: 0; left: 0; right: 0;
        padding: 44px 40px 70px 40px;
        background: linear-gradient(to bottom, rgba(0,0,0,0.78) 0%, rgba(0,0,0,0.35) 65%, transparent 100%);
    }}
    .severity-badge {{
        display: inline-block;
        background: {primary_color};
        color: {badge_text_color};
        font-size: 22px;
        font-weight: 700;
        padding: 6px 20px;
        border-radius: 6px;
        letter-spacing: 1.5px;
        margin-bottom: 16px;
    }}
    .update-badge {{
        display: inline-block;
        background: #FF3B30;
        color: #FFFFFF;
        font-size: 22px;
        font-weight: 800;
        padding: 6px 20px;
        border-radius: 6px;
        letter-spacing: 1.5px;
        margin-bottom: 16px;
        margin-left: 10px;
    }}
    .event-title {{
        color: #FFFFFF;
        font-size: 50px;
        font-weight: 800;
        line-height: 1.15;
        text-shadow: 0 2px 10px rgba(0,0,0,0.65);
    }}
    .event-icon {{
        font-size: 50px;
        margin-right: 12px;
        vertical-align: -6px;
    }}
    .bottom-panel {{
        position: absolute;
        bottom: 0; left: 0; right: 0;
        padding: 60px 40px 40px 40px;
        background: linear-gradient(to top, rgba(0,0,0,0.88) 0%, rgba(0,0,0,0.4) 65%, transparent 100%);
    }}
    .area-text {{
        color: #FFFFFF;
        font-size: 30px;
        font-weight: 600;
        margin-bottom: 12px;
        text-shadow: 0 1px 4px rgba(0,0,0,0.6);
    }}
    .expires-text {{
        color: #FFD166;
        font-size: 26px;
        font-weight: 700;
        margin-bottom: 18px;
        text-shadow: 0 1px 4px rgba(0,0,0,0.6);
    }}
    .source-text {{
        color: rgba(255,255,255,0.75);
        font-size: 18px;
        font-weight: 400;
        margin-bottom: 6px;
    }}
    .details-cue {{
        color: rgba(255,255,255,0.6);
        font-size: 16px;
        font-weight: 400;
    }}
</style>
</head>
<body>
    <img class="map-bg" src="{map_data_uri}" />
    <div class="top-banner">
        <div class="severity-badge">{action_phrase}</div>
        {update_html}
        <div class="event-title"><span class="event-icon">{icon}</span>{event}</div>
    </div>
    <div class="bottom-panel">
        <div class="area-text">{area_display}</div>
        {expires_html}
        <div class="source-text">Source: {sender}</div>
        <div class="details-cue">👇 Full details below</div>
    </div>
</body>
</html>"""


def render_html_to_image(html: str, output_path: str, width: int, height: int) -> None:
    """
    Renders an HTML string to a PNG using headless Chromium via Playwright.
    device_scale_factor=2 renders at retina density so all our text/gradients
    come out crisp regardless of the final display size.

    Requires (one-time local setup):
        pip install playwright
        playwright install chromium
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",  # avoids container /dev/shm crashes (default is only 64MB)
                "--disable-extensions",
                "--disable-background-networking",
                "--no-sandbox",             # needed in most restricted container environments
            ]
        )
        page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
        page.set_content(html, wait_until="load")
        page.screenshot(path=output_path)
        browser.close()


def generate_styled_facebook_image(
    feature: dict,
    token: str,
    output_filename: str = "fb_alert_styled.png",
    map_style: str = "streets-v12",
    preset_ratio: str = "portrait",
    padding: int = 140,
    is_update: bool = False,
) -> str:
    """
    Full pipeline: fetch a clean Mapbox background + alert polygon overlay,
    then composite a designed HTML/CSS layer on top (severity badge, event
    title, area, countdown, attribution) and render the result to a single
    final PNG via headless Chromium.

    Note label_scale is left at 1.0 here - the old "boost Mapbox's own
    labels" trick is no longer needed, since all the text that actually
    matters for the post is now our own crisp HTML, not the map's baked-in
    (resolution-capped) labels.
    """
    raw_width, raw_height = ASPECT_RATIOS.get(preset_ratio, (1080, 1280))
    tmp_map_path = "_tmp_map_background.png"

    generate_mapbox_nws_image(
        feature=feature,
        token=token,
        output_filename=tmp_map_path,
        map_style=map_style,
        preset_ratio=preset_ratio,
        padding=padding,
        label_scale=1.0,
    )

    props = feature.get("properties", {})
    palette = get_nws_colors(props.get("event", "Default"))
    html = build_html_template(feature, tmp_map_path, palette["color"], raw_width, raw_height, is_update=is_update)
    render_html_to_image(html, output_filename, raw_width, raw_height)

    print(f"✅ Saved styled Facebook post image to '{output_filename}' ({raw_width}x{raw_height})")
    return output_filename


# --- 10 TARGET STATES FOR INITIAL TESTING ---
# Tornado Alley (TX, OK, KS, NE) + Dixie Alley (MS, AL) + Gulf/Atlantic
# hurricane coast (FL, LA, NC) + severe-storm-prone Midwest (MO) - i.e. the
# states that generate NWS alerts most often and most severely. Swap freely.
# --- 10 TARGET STATES FOR INITIAL TESTING ---
# Tornado Alley (TX, OK, KS, NE) + Dixie Alley (MS, AL) + Gulf/Atlantic
# hurricane coast (FL, LA, NC) + severe-storm-prone Midwest (MO) - i.e. the
# states that generate NWS alerts most often and most severely. Swap freely.
# This list is only used to BOOTSTRAP the `destinations` collection in
# Firestore the first time - after that, Firestore itself is the source of
# truth for which states/destinations are active. Adding state #11 later
# means writing one new `destinations` document, not editing this list.
TARGET_STATES = ["TX", "OK", "KS", "NE", "FL", "LA", "MS", "AL", "MO", "NC"]

DISCORD_CONTENT_LIMIT = 2000  # Discord's hard cap on a webhook message's `content` field


def get_alert_id(feature: dict) -> str:
    """Pulls the stable unique id NWS assigns each alert (used as our dedup key)."""
    return feature.get("properties", {}).get("id") or feature.get("id") or ""


def extract_vtec_key(feature: dict) -> str:
    """
    Extracts a stable identifier for the underlying WARNING from its VTEC
    code, e.g. 'KMLB-RP-S-0031'. NWS issues a brand-new CAP `id` every time a
    warning is updated, extended, or corrected - so `id` alone isn't a
    reliable "have I already posted this" key, even though it's the same
    real-world event. The VTEC's office + phenomena + significance + event
    tracking number (ETN) stays constant across a warning's whole lifecycle
    (NEW -> CON -> EXT -> CAN), so THIS is the key that actually identifies
    "the same warning," regardless of how many times NWS re-issues it.
    Returns "" for product types that don't carry a VTEC (some plain
    statements) - those fall back to id-only dedup.
    """
    vtec_list = feature.get("properties", {}).get("parameters", {}).get("VTEC", [])
    if not vtec_list:
        return ""

    parts = vtec_list[0].strip("/").split(".")
    if len(parts) < 6:
        return ""
    _, _, office, phenomena, significance, etn = parts[:6]
    return f"{office}-{phenomena}-{significance}-{etn}"


def build_content_fingerprint(feature: dict) -> str:
    """
    A hash of the fields that matter for 'did this warning meaningfully
    change' - event, severity, area, expiry, headline. Used to tell a
    genuine update (expanded area, extended time, corrected text) apart from
    NWS simply re-serving the same still-active alert on the next poll.
    Deliberately excludes bookkeeping fields like `sent` that can shift
    without the actual warning content changing.
    """
    props = feature.get("properties", {})
    fingerprint_source = json.dumps(
        {
            "event": props.get("event", ""),
            "severity": props.get("severity", ""),
            "areaDesc": props.get("areaDesc", ""),
            "expires": props.get("expires", ""),
            "headline": props.get("headline", ""),
        },
        sort_keys=True,
    )
    return hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()


def safe_filename(alert_id: str) -> str:
    """Turns an NWS URN-style alert id into something filesystem-safe."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", alert_id)[-80:]


def fetch_alerts_for_states(states: list) -> list:
    """
    Fetches ALL currently active NWS alerts in ONE request (instead of one
    request per state), then filters locally to alerts touching any of
    `states` (via their UGC zone codes). This is 1 network call regardless
    of whether you're tracking 10 states or 50 - the per-state version
    scaled linearly with state count, this doesn't.

    Trade-off worth knowing: the response payload is now the full national
    feed (bigger single download) rather than 10 small targeted ones, and
    filtering relies on each alert carrying UGC codes - the vast majority
    do, but a malformed/edge-case product with empty geocode.UGC would be
    silently dropped here, whereas NWS's own server-side area filter might
    have still included it. Rare in practice for VTEC-carrying warnings.
    """
    url = "https://api.weather.gov/alerts/active"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        features = resp.json().get("features", [])
    except requests.RequestException as e:
        print(f"⚠️ Could not fetch active alerts: {e}")
        return []

    target_states = set(states)
    seen_ids = set()
    combined = []

    for f in features:
        if f.get("properties", {}).get("status") == "Test":
            continue
        if not (get_alert_touched_states(f) & target_states):
            continue
        alert_id = get_alert_id(f)
        if not alert_id or alert_id in seen_ids:
            continue
        seen_ids.add(alert_id)
        combined.append(f)

    return combined


def get_alert_touched_states(feature: dict) -> set:
    """
    ALL US states this alert's UGC codes touch (e.g. a warning straddling
    the FL/GA border touches both). Different from get_alert_state (singular,
    used for timezone display) - this is used for fan-out: which per-state
    destinations should receive this alert.
    """
    ugc_codes = feature.get("properties", {}).get("geocode", {}).get("UGC", [])
    return {code[:2].upper() for code in ugc_codes if len(code) >= 2}


# --- FIRESTORE DATA LAYER ---
# Two collections:
#   destinations   - one doc per state, keyed by state code. Holds where to
#                    post (discord webhook, or later an FB page + token) and
#                    two flags: `enabled` and `baseline_seeded`.
#   posted_alerts  - one doc per (state, warning) pair, keyed as
#                    "{state}_{vtec_key_or_alert_id}". This is what makes a
#                    border-spanning alert tracked independently per state -
#                    FL and GA each get their own row for the "same" warning,
#                    so posting to both (or updating just one) works correctly.

def get_firestore_client(project_id: str = None) -> "firestore.Client":
    from google.cloud import firestore
    return firestore.Client(project=project_id) if project_id else firestore.Client()


def posted_doc_id(state: str, platform: str, key: str) -> str:
    """Firestore document IDs can't contain '/' and have a length cap - sanitize + trim."""
    safe_key = re.sub(r"[^A-Za-z0-9._-]", "_", key)[-100:]
    return f"{state}_{platform}_{safe_key}"


def bootstrap_destination(db, state: str, platform: str, config: dict) -> None:
    """
    Adds one platform's config to a state's destination doc, WITHOUT
    touching any platform already configured for that state. This is what
    makes "add Instagram to FL next month" additive rather than a rewrite:
    FL's existing Discord/Facebook config and their baseline_seeded history
    stay exactly as they are - only the new platform gets appended, starting
    its own baseline_seeded=False.

    Each state document looks like:
      {
        "enabled": true,
        "platforms": {
          "discord":   {"discord_webhook_url": "..."},
          "facebook":  {"fb_page_id": "...", "fb_access_token": "..."},
          "instagram": {"ig_user_id": "...", "ig_access_token": "..."},
          "tiktok":    {"tiktok_access_token": "..."}
        },
        "baseline_seeded": {"discord": true, "facebook": false, ...}
      }
    """
    doc_ref = db.collection("destinations").document(state)
    snapshot = doc_ref.get()
    data = snapshot.to_dict() if snapshot.exists else {"enabled": True, "platforms": {}, "baseline_seeded": {}}

    if platform in data.get("platforms", {}):
        return  # already configured for this state - don't clobber

    data.setdefault("platforms", {})[platform] = config
    data.setdefault("baseline_seeded", {})[platform] = False
    doc_ref.set(data)
    print(f"➕ Added {platform} destination for {state}.")


def update_destination_platform(db, state: str, platform: str, **fields) -> None:
    """
    Updates one or more config fields on an EXISTING (state, platform)
    destination - e.g. swapping a webhook URL, or filling in real Facebook/
    Instagram/TikTok credentials once they're ready (replacing the empty
    placeholder values bootstrap_destination created). Unlike
    bootstrap_destination (which never overwrites), this is specifically
    for changing values on a platform that's already configured.

    Deliberately does NOT touch baseline_seeded - swapping in a new token
    for the same account isn't a reason to re-run the backlog-flood
    baseline seed; that's only for a platform's first-ever check.
    """
    dest_ref = db.collection("destinations").document(state)
    snapshot = dest_ref.get()
    if not snapshot.exists:
        raise ValueError(f"No destination doc exists for state '{state}' - run bootstrap_firestore.py first.")

    data = snapshot.to_dict()
    if platform not in data.get("platforms", {}):
        raise ValueError(
            f"State '{state}' has no '{platform}' platform configured yet - "
            f"use bootstrap_destination() to add it first, not this function."
        )

    updates = {f"platforms.{platform}.{key}": value for key, value in fields.items()}
    dest_ref.update(updates)
    print(f"✅ Updated {state}/{platform}: {list(fields.keys())}")


def get_enabled_destinations(db) -> dict:
    """Returns {state: destination_dict} for every enabled state (each dict holds its own `platforms`)."""
    docs = db.collection("destinations").where("enabled", "==", True).stream()
    return {doc.id: doc.to_dict() for doc in docs}


# Which config fields must be non-empty for a platform to be considered
# "actually set up" - lets you pre-create a placeholder entry (e.g.
# {"fb_page_id": "", "fb_access_token": ""}) now, so the structure already
# exists in Firestore, and flip it on later just by filling in real values -
# no code change, no re-bootstrapping, and no risk of the pipeline trying to
# post with empty credentials and erroring.
REQUIRED_PLATFORM_FIELDS = {
    "discord": ["discord_webhook_url"],
    "facebook": ["fb_page_id", "fb_access_token"],
    "instagram": ["ig_user_id", "ig_access_token"],
    "tiktok": ["tiktok_access_token"],
}


def is_platform_configured(platform: str, config: dict) -> bool:
    """True only if every required field for this platform is present and non-empty."""
    required = REQUIRED_PLATFORM_FIELDS.get(platform, [])
    return all(config.get(field) for field in required)


def get_posted_record(db, state: str, platform: str, vtec_key: str, alert_id: str):
    """Returns the stored dict for this (state, platform, warning), or None if never posted there."""
    if vtec_key:
        doc = db.collection("posted_alerts").document(posted_doc_id(state, platform, vtec_key)).get()
        if doc.exists:
            return doc.to_dict()
    doc = db.collection("posted_alerts").document(posted_doc_id(state, platform, alert_id)).get()
    return doc.to_dict() if doc.exists else None


def classify_alert_for_state(db, state: str, platform: str, feature: dict) -> str:
    """
    Returns 'new' / 'update' / 'unchanged' for this alert AT THIS SPECIFIC
    (state, platform) destination. Keying on platform too (not just state)
    means a dead TikTok token doesn't block Discord/Facebook for the same
    state - each platform tracks its own independent posting history, so a
    platform-specific failure only ever gets retried on that one platform.
    """
    alert_id = get_alert_id(feature)
    vtec_key = extract_vtec_key(feature)
    content_hash = build_content_fingerprint(feature)

    record = get_posted_record(db, state, platform, vtec_key, alert_id)
    if record is None:
        return "new"
    return "update" if record.get("content_hash") != content_hash else "unchanged"


def mark_posted_for_state(db, state: str, platform: str, feature: dict) -> None:
    alert_id = get_alert_id(feature)
    vtec_key = extract_vtec_key(feature)
    content_hash = build_content_fingerprint(feature)
    event = feature.get("properties", {}).get("event", "")
    key = vtec_key or alert_id

    db.collection("posted_alerts").document(posted_doc_id(state, platform, key)).set(
        {
            "state": state,
            "platform": platform,
            "alert_id": alert_id,
            "vtec_key": vtec_key,
            "content_hash": content_hash,
            "event": event,
            "posted_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def seed_baseline_for_platform_if_needed(db, state: str, platform: str, alerts_for_state: list) -> bool:
    """
    THE BACKLOG-FLOOD FIX, scoped to (state, platform): the first time a
    given platform is checked for a given state (not the first run of the
    whole script, and not even the first run for that state as a whole),
    NWS's backlog of active alerts for it gets marked as already-seen
    WITHOUT posting. This is what makes "add TikTok to FL, which already has
    Discord+Facebook running" seed itself cleanly for TikTok only, without
    re-flooding (or resetting) Discord/Facebook's already-established history.
    """
    dest_ref = db.collection("destinations").document(state)
    dest = dest_ref.get().to_dict() or {}
    if dest.get("baseline_seeded", {}).get(platform):
        return False

    print(f"🌱 First check for {state}/{platform} — seeding {len(alerts_for_state)} alert(s) as baseline (not posting).")
    for alert in alerts_for_state:
        mark_posted_for_state(db, state, platform, alert)
    dest_ref.update({f"baseline_seeded.{platform}": True})
    return True


def post_to_discord(webhook_url: str, image_path: str, caption: str) -> bool:
    """Posts the composited image + caption to a Discord channel via webhook. Returns True on success."""
    if len(caption) > DISCORD_CONTENT_LIMIT:
        caption = caption[: DISCORD_CONTENT_LIMIT - 1] + "…"

    with open(image_path, "rb") as f:
        files = {"file": (os.path.basename(image_path), f, "image/png")}
        payload = {"content": caption}
        resp = requests.post(webhook_url, data=payload, files=files, timeout=20)

    if resp.status_code not in (200, 204):
        print(f"❌ Discord post failed ({resp.status_code}): {resp.text}")
        return False
    print("✅ Posted to Discord")
    return True


def post_to_facebook(page_id: str, access_token: str, image_path: str, caption: str) -> bool:
    """
    Posts the composited image + caption to a Facebook Page via the Graph
    API's /{page_id}/photos endpoint. Unlike Instagram/TikTok, this DOES
    accept a direct file upload - no public image hosting needed, closest
    in shape to post_to_discord.

    Returns True only on a confirmed success (a post id back from the API);
    any error response - dead/expired token, revoked permission, rate limit -
    returns False so the caller leaves it unmarked, to be retried next cycle.
    A genuinely dead token will keep failing every retry until you notice
    and refresh it (see update_destination.py) - that's expected, not a bug;
    there's no way to distinguish "will succeed on retry" from "permanently
    broken" from the response alone, so both get the same safe treatment.
    """
    url = f"https://graph.facebook.com/v19.0/{page_id}/photos"
    try:
        with open(image_path, "rb") as f:
            files = {"source": (os.path.basename(image_path), f, "image/png")}
            data = {"caption": caption, "access_token": access_token}
            resp = requests.post(url, data=data, files=files, timeout=30)
    except requests.RequestException as e:
        print(f"❌ Facebook post failed (network error): {e}")
        return False

    if resp.status_code != 200:
        print(f"❌ Facebook post failed ({resp.status_code}): {resp.text}")
        return False

    result = resp.json()
    if "id" not in result and "post_id" not in result:
        print(f"❌ Facebook post response missing post id: {result}")
        return False

    print(f"✅ Posted to Facebook page {page_id}")
    return True


def post_to_instagram(ig_user_id: str, access_token: str, image_path: str, caption: str) -> bool:
    """
    Placeholder for Instagram's Content Publishing API - not implemented
    yet. Important architectural difference from Discord/Facebook:
    Instagram does NOT accept a direct file upload - it fetches the image
    itself via cURL, so `image_path` must already be hosted at a publicly
    reachable URL (e.g. a public Cloud Storage bucket) before this can be
    called for real. That's new infrastructure (a "publish the composited
    image somewhere public first" step), not just this one function.

    Real flow once ready:
      1. POST /{ig_user_id}/media with image_url + caption -> returns a
         creation_id (the container)
      2. Poll GET /{creation_id}?fields=status_code until FINISHED
      3. POST /{ig_user_id}/media_publish with creation_id -> publishes
    Rate limit: 100 published posts per IG account per rolling 24h.
    """
    raise NotImplementedError(
        "Instagram posting not implemented yet - requires the composited image to be hosted "
        "at a public URL first (IG fetches it via cURL, no direct upload), then a "
        "create-container -> poll-status -> publish sequence against the Graph API."
    )


def post_to_tiktok(access_token: str, image_path: str, caption: str) -> bool:
    """
    Placeholder for TikTok's Content Posting API - not implemented yet, and
    worth knowing upfront this is the heaviest lift of the four platforms:
      - Needs its own per-creator OAuth login (TikTok Login Kit), NOT an
        app-level page token like Facebook/Instagram - each state's TikTok
        account has to individually authorize your app.
      - Until your app passes TikTok's audit, ALL posts are forced private
        (unaudited clients are restricted to private-only) - there's no
        soft-launch the way Discord/FB allow; it's invisible until approved.
      - The image URL must be on a domain YOU'VE verified in the TikTok
        Developer Portal (DNS TXT record or meta tag) - an unverified
        CDN/bucket is rejected outright (url_ownership_unverified).
      - Rate limit: 6 requests/minute per user access token.

    Real flow once ready: POST /v2/post/publish/content/init/ with
    media_type=PHOTO, post_mode=DIRECT_POST, source_info.source=PULL_FROM_URL
    pointing at the verified-domain image URL.
    """
    raise NotImplementedError(
        "TikTok posting not implemented yet - needs per-creator OAuth (TikTok Login Kit), a "
        "domain you've verified in the TikTok Developer Portal to host the image, and app audit "
        "approval before posts are publicly visible (unaudited posts default to private)."
    )


def post_alert_to_platform(alert: dict, state: str, platform: str, config: dict, is_update: bool) -> bool:
    """
    Generates the styled image + caption once, then dispatches to whichever
    platform this call is for. Returns True only on CONFIRMED success - the
    caller uses this to decide whether to mark the alert as posted (a failed
    post must NOT be marked posted, so it gets retried next cycle instead of
    silently vanishing).
    """
    alert_id = get_alert_id(alert)
    out_path = f"_pipeline_out_{safe_filename(state + '_' + platform + '_' + alert_id)}.png"
    success = False
    try:
        generate_styled_facebook_image(
            feature=alert,
            token=MAPBOX_ACCESS_TOKEN,
            output_filename=out_path,
            map_style="streets-v12",
            preset_ratio="portrait",
            padding=140,
            is_update=is_update,
        )
        caption = build_facebook_caption(alert, is_update=is_update)

        if platform == "discord":
            success = post_to_discord(config["discord_webhook_url"], out_path, caption)
        elif platform == "facebook":
            success = post_to_facebook(config["fb_page_id"], config["fb_access_token"], out_path, caption)
        elif platform == "instagram":
            success = post_to_instagram(config["ig_user_id"], config["ig_access_token"], out_path, caption)
        elif platform == "tiktok":
            success = post_to_tiktok(config["tiktok_access_token"], out_path, caption)
        else:
            print(f"⚠️ Unknown platform '{platform}' for '{state}' - skipping.")
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)
    return success


def run_alert_pipeline(db, states: list) -> None:
    """
    Full run: look up which of `states` have an enabled Firestore
    destination, fetch active alerts across them, fan each alert out to
    every state it touches (border-spanning alerts hit multiple states) AND
    every platform configured for that state, classify per (state,
    platform) as new/update/unchanged, and post accordingly. A failed post
    is NOT marked posted, so it's naturally retried on the next poll cycle
    instead of silently disappearing - and a failure on one platform never
    blocks the others for the same state.
    """
    destinations = get_enabled_destinations(db)
    target_states = [s for s in states if s in destinations]
    if not target_states:
        print("No enabled destinations configured for the requested states - nothing to do.")
        return

    alerts = fetch_alerts_for_states(target_states)
    print(f"Fetched {len(alerts)} unique active alert(s) across {len(target_states)} destination state(s).")

    alerts_by_state = {state: [] for state in target_states}
    for alert in alerts:
        for state in get_alert_touched_states(alert) & set(target_states):
            alerts_by_state[state].append(alert)

    for state, state_alerts in alerts_by_state.items():
        platforms = destinations[state].get("platforms", {})

        for platform, config in platforms.items():
            if not is_platform_configured(platform, config):
                continue  # placeholder entry (empty values) - not active yet, skip entirely

            if seed_baseline_for_platform_if_needed(db, state, platform, state_alerts):
                continue  # this (state, platform) was just baseline-seeded - post nothing this run

            for alert in state_alerts:
                classification = classify_alert_for_state(db, state, platform, alert)
                if classification == "unchanged":
                    continue

                is_update = classification == "update"
                event = alert.get("properties", {}).get("event", "Weather Alert")
                alert_id = get_alert_id(alert)
                tag = "UPDATE" if is_update else "NEW"

                try:
                    success = post_alert_to_platform(alert, state, platform, config, is_update)
                    if success:
                        mark_posted_for_state(db, state, platform, alert)
                        print(f"✅ [{tag}] {event} -> {state}/{platform}")
                    else:
                        print(f"⚠️ Post failed for '{event}' -> {state}/{platform} - will retry next cycle (not marked posted).")
                except Exception as e:
                    # One bad alert/platform (oversized geometry edge case, network hiccup,
                    # dead token) shouldn't take down the whole run, or affect other platforms
                    # for this same state - log and move on. Not marked posted, so it retries.
                    print(f"⚠️ Failed to process alert '{event}' ({alert_id}) for {state}/{platform}: {e}")


if __name__ == "__main__":
    # Local continuous-loop test mode ONLY - Cloud Run doesn't run this block
    # at all (see app.py, which calls run_alert_pipeline() once per HTTP
    # trigger instead). Useful for testing locally against real Firestore
    # before deploying. Destinations must already exist - run
    # bootstrap_firestore.py once first if you haven't.
    db = get_firestore_client()  # uses GOOGLE_APPLICATION_CREDENTIALS or gcloud auth application-default login

    POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))

    print(f"Starting continuous polling every {POLL_INTERVAL_SECONDS}s across {TARGET_STATES}...")
    while True:
        try:
            run_alert_pipeline(db, states=TARGET_STATES)
        except Exception as e:
            # A bad cycle (network blip, NWS hiccup, Firestore hiccup) shouldn't
            # kill the whole process - log it and try again next interval.
            print(f"⚠️ Pipeline run failed: {e}")

        print(f"Sleeping {POLL_INTERVAL_SECONDS}s until next check...\n")
        time.sleep(POLL_INTERVAL_SECONDS)

    print(f"Starting continuous polling every {POLL_INTERVAL_SECONDS}s across {TARGET_STATES}...")
    while True:
        try:
            run_alert_pipeline(db, states=TARGET_STATES)
        except Exception as e:
            # A bad cycle (network blip, NWS hiccup, Firestore hiccup) shouldn't
            # kill the whole process - log it and try again next interval.
            print(f"⚠️ Pipeline run failed: {e}")

        print(f"Sleeping {POLL_INTERVAL_SECONDS}s until next check...\n")
        time.sleep(POLL_INTERVAL_SECONDS)
