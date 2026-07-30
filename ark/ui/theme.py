"""
Ark UI — Classical visual theme (styling only).

This module contains NO scoring, matching, pipeline, persistence or browsing
logic, and it never changes what any page renders, in what order, or how any
control behaves. Its entire job is to (a) inject one stylesheet into the
Streamlit page and (b) register a matching Altair chart theme, so the existing
widgets that ark/ui/app.py and ark/ui/pages/1_Project_Browser.py already emit
are drawn in the Classical palette and typography instead of Streamlit's
defaults.

Usage — one call, immediately after st.set_page_config() on each page::

    from ark.ui.theme import apply_theme
    apply_theme()

Everything below is cosmetic: colors, fonts, spacing, radii, borders and hover
states. Nothing here reads, computes, filters or transforms experiment data.
"""

from __future__ import annotations

import streamlit as st

# --- Classical design tokens (kept here only so the Altair theme and the
# --- stylesheet below cannot drift apart). Values copied verbatim from the
# --- design system's styles.css :root block.
BG = "#f3f2f2"
SURFACE = "#eae9e9"
TEXT = "#201f1d"
ACCENT = "#b68235"
ACCENT_400 = "#e1ad66"
ACCENT_600 = "#a06f24"
ACCENT_700 = "#7d5411"
DIVIDER = "#d7d3d3"
NEUTRAL_500 = "#9b9797"
NEUTRAL_700 = "#605d5d"
NEUTRAL_900 = "#2d2b2b"

FONT_HEADING = "Cormorant Garamond, Georgia, serif"
FONT_BODY = "Lora, Georgia, serif"

#: Ordered categorical palette for charts — accent-led, neutral-supported,
#: so multi-series charts stay in the system's mono-accent register.
CHART_PALETTE = [
    ACCENT,
    NEUTRAL_700,
    ACCENT_700,
    NEUTRAL_500,
    ACCENT_400,
    NEUTRAL_900,
]

_CSS = """\
/* ============================================================================
   Ark — Classical theme (visual layer only)
   Derived entirely from the Classical design system tokens.
   Cosmetic CSS only: no structural, navigational or behavioural change.
   ========================================================================== */

@import url("https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,300..700;1,300..700&family=Lora:ital,wght@0,400..700;1,400..700&display=swap");

:root,
.stApp {
  --color-bg: #f3f2f2;
  --color-surface: #eae9e9;
  --color-text: #201f1d;
  --color-accent: #b68235;
  --color-divider: rgba(32, 31, 29, 0.16);

  --color-neutral-100: #f8f4f4;
  --color-neutral-200: #eae7e7;
  --color-neutral-300: #d7d3d3;
  --color-neutral-400: #bab6b6;
  --color-neutral-500: #9b9797;
  --color-neutral-600: #7d7979;
  --color-neutral-700: #605d5d;
  --color-neutral-800: #444141;
  --color-neutral-900: #2d2b2b;

  --color-accent-100: #fff3e4;
  --color-accent-200: #ffe3bf;
  --color-accent-300: #facb8d;
  --color-accent-400: #e1ad66;
  --color-accent-500: #c28d41;
  --color-accent-600: #a06f24;
  --color-accent-700: #7d5411;
  --color-accent-800: #5a3b0a;
  --color-accent-900: #3a270d;

  --font-heading: "Cormorant Garamond", Georgia, serif;
  --font-heading-weight: 600;
  --font-body: "Lora", Georgia, serif;
  --font-mono: "SFMono-Regular", "JetBrains Mono", ui-monospace, Menlo, monospace;

  --space-1: 4.6px;
  --space-2: 9.2px;
  --space-3: 13.8px;
  --space-4: 18.4px;
  --space-6: 27.6px;
  --space-8: 36.8px;

  --radius-sm: 2px;
  --radius-md: 4px;
  --radius-lg: 7px;

  --shadow-sm: 0 1px 2px rgba(45, 43, 43, 0.14);
  --shadow-md: 0 3px 10px rgba(45, 43, 43, 0.16);
  --shadow-lg: 0 12px 32px rgba(45, 43, 43, 0.22);
}

/* ---------- ground ------------------------------------------------------- */

.stApp,
[data-testid="stAppViewContainer"],
[data-testid="stMain"] {
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-body);
}

[data-testid="stHeader"],
[data-testid="stToolbar"] {
  background: transparent;
}

.stApp,
.stApp p,
.stApp li,
.stApp label,
.stApp span,
.stApp div {
  font-family: var(--font-body);
}

/* Streamlit's own Material Symbols icons (sidebar collapse arrow, expander
   chevrons, dataframe sort arrows, etc.) are ligature-rendered spans whose
   text content is literally the icon name (e.g. "keyboard_double_arrow_right").
   The blanket span/div font-family rule above overrides their icon font,
   which makes that literal name show up as text instead of a glyph -- this
   restores Streamlit's own icon font for those spans specifically, without
   touching the body-text override above for anything else. */
[data-testid="stIconMaterial"] {
  font-family: "Material Symbols Rounded" !important;
}

.stApp p,
.stApp li {
  color: var(--color-neutral-800);
  line-height: 1.62;
  text-wrap: pretty;
}

.stApp a {
  color: var(--color-accent-700);
  text-decoration: underline;
  text-underline-offset: 2px;
  text-decoration-thickness: 1px;
  text-decoration-color: var(--color-accent-300);
}

.stApp a:hover {
  color: var(--color-accent-800);
  text-decoration-color: var(--color-accent-600);
}

.stApp :focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.stApp ::selection {
  background: var(--color-accent-200);
  color: var(--color-accent-900);
}

/* ---------- type --------------------------------------------------------- */

.stApp h1,
.stApp h2,
.stApp h3,
.stApp h4,
.stApp h5,
.stApp h6 {
  font-family: var(--font-heading);
  color: var(--color-text);
  letter-spacing: 0.005em;
  font-feature-settings: "tnum";
  text-wrap: balance;
}

.stApp h1 {
  font-weight: 400;
  font-size: 3.05rem;
  line-height: 1.08;
  padding-bottom: var(--space-2);
  margin-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-divider);
}

.stApp h2 {
  font-weight: 500;
  font-size: 1.95rem;
  line-height: 1.2;
  margin-top: var(--space-8);
  padding-bottom: var(--space-1);
  border-bottom: 1px solid var(--color-divider);
}

.stApp h3 {
  font-weight: var(--font-heading-weight);
  font-size: 1.42rem;
  line-height: 1.25;
  margin-top: var(--space-6);
}

.stApp h4,
.stApp h5,
.stApp h6 {
  font-weight: var(--font-heading-weight);
  font-size: 1.12rem;
}

/* captions — the small italic marginalia of the page */
[data-testid="stCaptionContainer"],
.stCaption,
.stApp small {
  font-family: var(--font-body);
  font-style: italic;
  color: var(--color-neutral-700) !important;
  font-size: 0.86rem;
  line-height: 1.55;
}

[data-testid="stCaptionContainer"] p,
.stCaption p {
  color: var(--color-neutral-700) !important;
}

.stApp strong,
.stApp b {
  font-weight: 600;
  color: var(--color-text);
}

.stApp code,
.stApp kbd {
  font-family: var(--font-mono);
  font-size: 0.84em;
  color: var(--color-accent-800);
  background: var(--color-accent-100);
  border: 1px solid var(--color-accent-200);
  border-radius: var(--radius-sm);
  padding: 0.05em 0.34em;
}

/* rules */
.stApp hr,
[data-testid="stMarkdownContainer"] hr {
  border: none;
  border-top: 1px solid var(--color-divider);
  margin: var(--space-6) 0;
}

/* ---------- sidebar ------------------------------------------------------ */

[data-testid="stSidebar"],
[data-testid="stSidebarContent"] {
  background: var(--color-neutral-100);
  border-right: 1px solid var(--color-divider);
}

[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
  font-size: 1.3rem;
  font-weight: var(--font-heading-weight);
  margin-top: var(--space-4);
  border-bottom: 1px solid var(--color-divider);
  padding-bottom: var(--space-1);
}

[data-testid="stSidebarNav"] {
  border-bottom: 1px solid var(--color-divider);
  padding-bottom: var(--space-2);
}

[data-testid="stSidebarNav"] a {
  border-radius: var(--radius-md);
  text-decoration: none;
}

[data-testid="stSidebarNav"] a span {
  font-family: var(--font-heading);
  font-size: 1.02rem;
  color: var(--color-neutral-800);
}

[data-testid="stSidebarNav"] a:hover {
  background: var(--color-accent-100);
}

[data-testid="stSidebarNav"] li > div > a[aria-current="page"],
[data-testid="stSidebarNav"] a[aria-current="page"] {
  background: transparent;
  box-shadow: inset 2px 0 0 var(--color-accent);
}

[data-testid="stSidebarNav"] a[aria-current="page"] span {
  color: var(--color-accent-800);
}

/* ---------- buttons — outlined, never filled ----------------------------- */

.stButton > button,
.stDownloadButton > button,
.stFormSubmitButton > button,
[data-testid="stBaseButton-secondary"],
[data-testid="stBaseButton-primary"] {
  font-family: var(--font-heading);
  font-weight: var(--font-heading-weight);
  font-size: 0.98rem;
  letter-spacing: 0.02em;
  color: var(--color-accent-800);
  background: transparent;
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-4);
  box-shadow: none;
  transition: background-color 120ms ease, border-color 120ms ease, color 120ms ease;
}

.stButton > button:hover,
.stDownloadButton > button:hover,
[data-testid="stBaseButton-secondary"]:hover,
[data-testid="stBaseButton-primary"]:hover {
  background: var(--color-accent-100);
  border-color: var(--color-accent-600);
  color: var(--color-accent-900);
}

.stButton > button:active,
.stDownloadButton > button:active,
[data-testid="stBaseButton-primary"]:active {
  background: var(--color-accent-200);
  border-color: var(--color-accent-700);
}

.stButton > button[kind="primary"],
[data-testid="stBaseButton-primary"] {
  border-width: 1px;
  border-color: var(--color-accent-600);
  color: var(--color-accent-900);
  background: transparent;
}

.stButton > button:disabled,
.stDownloadButton > button:disabled {
  opacity: 0.45;
}

/* the file-tree buttons in the Project Browser read as an index, not as controls */
[data-testid="stSidebar"] .stButton > button,
[data-testid="column"] .stButton > button {
  text-align: left;
  justify-content: flex-start;
}

/* ---------- inputs ------------------------------------------------------- */

.stTextInput input,
.stNumberInput input,
.stTextArea textarea,
[data-baseweb="input"],
[data-baseweb="base-input"] {
  font-family: var(--font-body);
  background: var(--color-neutral-100) !important;
  color: var(--color-text) !important;
  border-radius: var(--radius-md);
}

.stTextInput [data-baseweb="input"],
.stNumberInput [data-baseweb="input"],
.stTextArea [data-baseweb="textarea"],
[data-baseweb="select"] > div:first-child {
  background: var(--color-neutral-100);
  border: 1px solid var(--color-neutral-300);
  border-radius: var(--radius-md);
  transition: border-color 120ms ease, box-shadow 120ms ease;
}

.stTextInput [data-baseweb="input"]:hover,
.stNumberInput [data-baseweb="input"]:hover,
[data-baseweb="select"] > div:first-child:hover {
  border-color: var(--color-accent-400);
}

.stTextInput [data-baseweb="input"]:focus-within,
.stNumberInput [data-baseweb="input"]:focus-within,
[data-baseweb="select"] > div:first-child:focus-within {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent-300);
}

.stApp label,
.stApp [data-testid="stWidgetLabel"] p {
  font-family: var(--font-heading);
  font-weight: var(--font-heading-weight);
  font-size: 1rem;
  color: var(--color-neutral-900) !important;
  letter-spacing: 0.01em;
}

/* select menu */
[data-baseweb="popover"] [role="listbox"],
[data-baseweb="menu"] {
  background: var(--color-neutral-100);
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  font-family: var(--font-body);
}

[data-baseweb="menu"] li[aria-selected="true"],
[data-baseweb="menu"] li:hover {
  background: var(--color-accent-100);
  color: var(--color-accent-900);
}

/* radio + checkbox */
.stRadio [data-baseweb="radio"] div[aria-checked="true"] > div,
.stRadio [role="radio"][aria-checked="true"] [data-baseweb="radio"] > div:first-child,
.stRadio [role="radio"][aria-checked="true"] > div:first-child {
  background-color: var(--color-accent) !important;
  border-color: var(--color-accent) !important;
}

.stRadio [role="radio"][aria-checked="true"] {
  background-color: transparent !important;
  color: var(--color-accent-900);
}

.stRadio [data-baseweb="radio"] > div:first-child {
  border-color: var(--color-neutral-400);
}

.stCheckbox [data-baseweb="checkbox"] span[data-testid="stCheckbox"],
.stCheckbox [role="checkbox"][aria-checked="true"] > div,
.stCheckbox [data-baseweb="checkbox"] div[aria-checked="true"] {
  background-color: var(--color-accent) !important;
  border-color: var(--color-accent) !important;
}

.stSlider [data-baseweb="slider"] [role="slider"] {
  background: var(--color-accent) !important;
}

/* ---------- metrics — figures set as plate captions ---------------------- */

[data-testid="stMetric"] {
  background: transparent;
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  min-width: 0;
  overflow: hidden;
}

[data-testid="column"] {
  min-width: 0;
}

[data-testid="stMetricLabel"],
[data-testid="stMetricLabel"] p {
  font-family: var(--font-heading);
  font-weight: var(--font-heading-weight);
  font-size: 0.9rem !important;
  color: var(--color-neutral-700) !important;
  letter-spacing: 0.015em;
  line-height: 1.3;
  min-width: 0;
  overflow-wrap: anywhere;
  hyphens: auto;
}

[data-testid="stMetricValue"],
[data-testid="stMetricValue"] div {
  font-family: var(--font-heading);
  font-weight: 400;
  font-size: clamp(1.25rem, 1.6vw + 0.5rem, 1.85rem);
  line-height: 1.15;
  color: var(--color-text);
  font-feature-settings: "tnum";
  min-width: 0;
  max-width: 100%;
  overflow-wrap: anywhere;
  white-space: normal;
}

[data-testid="stMetricDelta"] {
  font-family: var(--font-body);
  font-size: 0.85rem;
}

/* ---------- expanders ---------------------------------------------------- */

[data-testid="stExpander"] {
  background: transparent;
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  box-shadow: none;
  overflow: hidden;
}

[data-testid="stExpander"] details,
[data-testid="stExpander"] > details {
  background: transparent;
  border: none;
}

[data-testid="stExpander"] summary {
  font-family: var(--font-heading);
  font-weight: var(--font-heading-weight);
  font-size: 1.02rem;
  color: var(--color-neutral-900);
  padding: var(--space-2) var(--space-4);
  transition: background-color 120ms ease;
}

[data-testid="stExpander"] summary:hover {
  background: var(--color-accent-100);
  color: var(--color-accent-900);
}

[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
  border-top: 1px solid var(--color-divider);
  padding-top: var(--space-3);
}

/* ---------- tables ------------------------------------------------------- */

[data-testid="stTable"] table,
.stApp [data-testid="stMarkdownContainer"] table {
  width: 100%;
  border-collapse: collapse;
  font-family: var(--font-body);
  font-size: 0.92rem;
  font-feature-settings: "tnum";
  background: transparent;
  border: none;
}

[data-testid="stTable"] thead tr th,
.stApp [data-testid="stMarkdownContainer"] table thead th {
  font-family: var(--font-heading);
  font-weight: var(--font-heading-weight);
  font-size: 0.86rem;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--color-neutral-700);
  background: transparent;
  border: none;
  border-bottom: 1px solid var(--color-text);
  padding: var(--space-2) var(--space-3);
  text-align: left;
}

[data-testid="stTable"] tbody tr td,
[data-testid="stTable"] tbody tr th,
.stApp [data-testid="stMarkdownContainer"] table tbody td {
  border: none;
  border-bottom: 1px solid var(--color-divider);
  padding: var(--space-2) var(--space-3);
  color: var(--color-neutral-800);
  background: transparent;
}

[data-testid="stTable"] tbody tr:hover td,
[data-testid="stTable"] tbody tr:hover th {
  background: var(--color-accent-100);
}

/* dataframes (glide grid) take their palette from CSS custom properties */
[data-testid="stDataFrame"],
[data-testid="stDataFrameResizable"] {
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  --gdg-accent-color: var(--color-accent);
  --gdg-accent-fg: #ffffff;
  --gdg-accent-light: var(--color-accent-100);
  --gdg-bg-cell: var(--color-bg);
  --gdg-bg-cell-medium: var(--color-neutral-100);
  --gdg-bg-header: var(--color-neutral-200);
  --gdg-bg-header-hovered: var(--color-accent-100);
  --gdg-bg-header-has-focus: var(--color-accent-200);
  --gdg-border-color: var(--color-divider);
  --gdg-horizontal-border-color: var(--color-divider);
  --gdg-text-dark: var(--color-text);
  --gdg-text-medium: var(--color-neutral-700);
  --gdg-text-light: var(--color-neutral-600);
  --gdg-text-header: var(--color-neutral-700);
  --gdg-font-family: var(--font-body);
  --gdg-header-font-style: 600 12px;
  --gdg-base-font-style: 13px;
}

/* ---------- code blocks -------------------------------------------------- */

[data-testid="stCodeBlock"],
.stCode {
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  background: var(--color-neutral-100);
}

[data-testid="stCodeBlock"] pre,
.stCode pre {
  background: transparent !important;
  padding: var(--space-3) var(--space-4) !important;
}

[data-testid="stCodeBlock"] code,
.stCode code {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  line-height: 1.6;
  background: transparent;
  border: none;
  padding: 0;
  color: var(--color-neutral-900);
}

/* syntax tokens, tuned to the accent rather than a rainbow */
[data-testid="stCodeBlock"] .token.tag,
[data-testid="stCodeBlock"] .token.keyword,
[data-testid="stCodeBlock"] .token.key {
  color: var(--color-accent-700);
}

[data-testid="stCodeBlock"] .token.attr-name {
  color: var(--color-neutral-600);
}

[data-testid="stCodeBlock"] .token.string,
[data-testid="stCodeBlock"] .token.attr-value {
  color: var(--color-neutral-800);
  font-style: italic;
}

[data-testid="stCodeBlock"] .token.comment,
[data-testid="stCodeBlock"] .token.punctuation {
  color: var(--color-neutral-500);
}

[data-testid="stCodeBlock"] button {
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-sm);
  background: var(--color-bg);
}

/* ---------- alerts — bordered, lightly tinted ---------------------------- */

[data-testid="stAlert"],
[data-testid="stNotification"] {
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  background: var(--color-neutral-100);
  color: var(--color-neutral-900);
  box-shadow: none;
  padding: var(--space-3) var(--space-4);
  font-family: var(--font-body);
}

[data-testid="stAlert"] p,
[data-testid="stNotification"] p {
  color: var(--color-neutral-900);
}

[data-testid="stAlertContentInfo"],
[data-testid="stAlert"] [data-baseweb="notification"][kind="info"] {
  background: var(--color-neutral-100);
  border-color: var(--color-neutral-400);
}

[data-testid="stAlertContentSuccess"],
[data-testid="stAlert"] [data-baseweb="notification"][kind="positive"] {
  background: #eef2ec;
  border-color: #93a58c;
}

[data-testid="stAlertContentWarning"],
[data-testid="stAlert"] [data-baseweb="notification"][kind="warning"] {
  background: var(--color-accent-100);
  border-color: var(--color-accent-400);
}

[data-testid="stAlertContentError"],
[data-testid="stAlert"] [data-baseweb="notification"][kind="negative"] {
  background: #f6e9e7;
  border-color: #b98d86;
}

/* ---------- tabs --------------------------------------------------------- */

.stTabs [data-baseweb="tab-list"] {
  gap: var(--space-4);
  border-bottom: 1px solid var(--color-divider);
  background: transparent;
}

.stTabs [data-baseweb="tab"] {
  font-family: var(--font-heading);
  font-weight: var(--font-heading-weight);
  color: var(--color-neutral-600);
  background: transparent;
  padding: var(--space-2) 0;
}

.stTabs [data-baseweb="tab"][aria-selected="true"] {
  color: var(--color-accent-800);
}

.stTabs [data-baseweb="tab-highlight"] {
  background: var(--color-accent);
}

/* ---------- charts ------------------------------------------------------- */

[data-testid="stVegaLiteChart"],
[data-testid="stArrowVegaLiteChart"] {
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  background: transparent;
}

.stSpinner > div {
  border-top-color: var(--color-accent) !important;
}

[data-testid="stProgressBar"] > div > div > div {
  background: var(--color-accent);
}

/* ---------- difflib diff table (Project Browser) ------------------------- */
/* Overrides the dark inline block emitted by browser_logic._DIFF_TABLE_CSS,
   without editing that module. */

.stApp table.diff {
  font-family: var(--font-mono) !important;
  font-size: 0.78rem !important;
  border: 1px solid var(--color-divider) !important;
  border-collapse: collapse !important;
  background: var(--color-bg) !important;
}

.stApp table.diff td,
.stApp table.diff th {
  border: none !important;
  border-bottom: 1px solid var(--color-divider) !important;
  color: var(--color-neutral-800) !important;
  padding: 2px 8px !important;
}

.stApp table.diff .diff_header,
.stApp table.diff td.diff_header,
.stApp table.diff th {
  background-color: var(--color-neutral-200) !important;
  color: var(--color-neutral-600) !important;
  font-family: var(--font-heading) !important;
}

.stApp table.diff .diff_next {
  background-color: var(--color-neutral-200) !important;
}

.stApp table.diff .diff_add {
  background-color: #e6efe4 !important;
  color: #2f4a33 !important;
}

.stApp table.diff .diff_chg {
  background-color: var(--color-accent-100) !important;
  color: var(--color-accent-800) !important;
}

.stApp table.diff .diff_sub {
  background-color: #f5e5e2 !important;
  color: #6b2f28 !important;
}
"""


def _altair_theme() -> dict:
    """Vega-Lite config matching the Classical tokens (fonts, axes, palette)."""
    return {
        "config": {
            "background": "transparent",
            "font": FONT_BODY,
            "arc": {"fill": ACCENT},
            "area": {"fill": ACCENT},
            "bar": {"fill": ACCENT},
            "line": {"stroke": ACCENT, "strokeWidth": 1.5},
            "point": {"fill": ACCENT, "stroke": ACCENT},
            "circle": {"fill": ACCENT},
            "rect": {"fill": ACCENT},
            "axis": {
                "domainColor": DIVIDER,
                "gridColor": DIVIDER,
                "gridOpacity": 0.55,
                "tickColor": DIVIDER,
                "labelColor": NEUTRAL_700,
                "labelFont": FONT_BODY,
                "labelFontSize": 11,
                "titleColor": NEUTRAL_900,
                "titleFont": FONT_HEADING,
                "titleFontWeight": 600,
                "titleFontSize": 13,
            },
            "legend": {
                "labelColor": NEUTRAL_700,
                "labelFont": FONT_BODY,
                "titleColor": NEUTRAL_900,
                "titleFont": FONT_HEADING,
                "titleFontWeight": 600,
            },
            "title": {
                "color": TEXT,
                "font": FONT_HEADING,
                "fontWeight": 600,
                "fontSize": 16,
                "anchor": "start",
            },
            "view": {"stroke": "transparent"},
            "range": {
                "category": CHART_PALETTE,
                "ordinal": CHART_PALETTE,
                "ramp": ["#fff3e4", ACCENT, "#3a270d"],
            },
        }
    }


def _register_altair_theme() -> None:
    """Register + enable the chart theme, across Altair 5.0-5.5+ APIs.

    Purely a default-styling registration: any chart that sets its own color
    explicitly keeps that color, and no chart's data, encoding or type changes.
    """
    try:
        import altair as alt
    except Exception:  # pragma: no cover -- altair ships with streamlit
        return

    name = "ark_classical"
    try:  # Altair >= 5.5
        alt.theme.register(name, enable=True)(_altair_theme)
        return
    except Exception:
        pass
    try:  # Altair 5.0 - 5.4
        alt.themes.register(name, _altair_theme)
        alt.themes.enable(name)
    except Exception:  # pragma: no cover -- never break a page over styling
        pass


def apply_theme() -> None:
    """Inject the Classical stylesheet and enable the matching chart theme.

    Idempotent and side-effect-free beyond styling. Safe to call at the top of
    every page, right after st.set_page_config().
    """
    _register_altair_theme()
    st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)
