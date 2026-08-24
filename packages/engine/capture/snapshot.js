// The single DOM walk that produces elements.json (SPEC §4.1).
//
// Only the properties listed in §4.1 are read. Never dump full computed style — it is
// ~340 properties per element and makes artifacts unusable.
//
// Returns records with `stableKey: ""`; Python fills it in so the hash has exactly one
// implementation (SPEC §8.2).
(options) => {
  const SKIP = new Set([
    "SCRIPT", "STYLE", "META", "LINK", "HEAD", "NOSCRIPT", "TEMPLATE", "TITLE", "BASE",
    "HTML", "PARAM", "SOURCE", "TRACK",
  ]);

  const r2 = (n) => Math.round(n * 100) / 100;
  const num = (v) => {
    const n = parseFloat(v);
    return Number.isFinite(n) ? r2(n) : 0;
  };
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

  // ---------------------------------------------------------------- colour maths

  function parseColour(value) {
    const m = /^rgba?\(([^)]+)\)$/.exec(value || "");
    if (!m) return null;
    const parts = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
    if (parts.length < 3 || parts.some((p) => !Number.isFinite(p))) return null;
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  }

  function over(top, bottom) {
    const a = top.a + bottom.a * (1 - top.a);
    if (a === 0) return { r: 0, g: 0, b: 0, a: 0 };
    const mix = (t, b) => (t * top.a + b * bottom.a * (1 - top.a)) / a;
    return { r: mix(top.r, bottom.r), g: mix(top.g, bottom.g), b: mix(top.b, bottom.b), a };
  }

  const toCss = (c) =>
    `rgb(${Math.round(c.r)}, ${Math.round(c.g)}, ${Math.round(c.b)})`;

  function luminance(c) {
    const chan = (v) => {
      const s = v / 255;
      return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * chan(c.r) + 0.7152 * chan(c.g) + 0.0722 * chan(c.b);
  }

  function contrastRatio(fg, bg) {
    const a = luminance(fg) + 0.05;
    const b = luminance(bg) + 0.05;
    return r2(a > b ? a / b : b / a);
  }

  // The effective background behind an element: composite the ancestor chain from the
  // root upward, so a translucent card over a dark section resolves correctly.
  function resolvedBackground(el) {
    const chain = [];
    for (let n = el; n; n = n.parentElement) chain.push(getComputedStyle(n).backgroundColor);
    let base = { r: 255, g: 255, b: 255, a: 1 };
    for (let i = chain.length - 1; i >= 0; i--) {
      const c = parseColour(chain[i]);
      if (c && c.a > 0) base = over(c, base);
    }
    return base;
  }

  // ------------------------------------------------------------------ ARIA roles

  const INPUT_ROLES = {
    button: "button", submit: "button", reset: "button", image: "button",
    checkbox: "checkbox", radio: "radio", range: "slider", number: "spinbutton",
    search: "searchbox", email: "textbox", tel: "textbox", text: "textbox", url: "textbox",
  };
  const TAG_ROLES = {
    A: null, ARTICLE: "article", ASIDE: "complementary", BUTTON: "button", DD: "definition",
    DETAILS: "group", DIALOG: "dialog", DL: "list", DT: "term", FIELDSET: "group",
    FIGURE: "figure", FOOTER: "contentinfo", FORM: "form", H1: "heading", H2: "heading",
    H3: "heading", H4: "heading", H5: "heading", H6: "heading", HEADER: "banner",
    HR: "separator", IMG: "img", LI: "listitem", MAIN: "main", NAV: "navigation",
    OL: "list", OPTION: "option", OUTPUT: "status", PROGRESS: "progressbar", P: "paragraph",
    SECTION: null, SELECT: "combobox", SUMMARY: "button", TABLE: "table", TBODY: "rowgroup",
    TD: "cell", TEXTAREA: "textbox", TH: "columnheader", THEAD: "rowgroup", TR: "row",
    UL: "list",
  };

  function computedRole(el) {
    const explicit = norm(el.getAttribute("role"));
    if (explicit) return explicit.split(" ")[0];
    const tag = el.tagName;
    if (tag === "A") return el.hasAttribute("href") ? "link" : null;
    if (tag === "INPUT") return INPUT_ROLES[(el.type || "text").toLowerCase()] || "textbox";
    if (tag === "SECTION") {
      return el.hasAttribute("aria-label") || el.hasAttribute("aria-labelledby")
        ? "region"
        : null;
    }
    if (tag === "FOOTER" || tag === "HEADER") {
      return el.closest("article, aside, main, nav, section") ? null : TAG_ROLES[tag];
    }
    return TAG_ROLES[tag] === undefined ? null : TAG_ROLES[tag];
  }

  const LANDMARKS =
    "main, nav, header, footer, aside, form, section[aria-label], section[aria-labelledby]," +
    "[role=main], [role=navigation], [role=banner], [role=contentinfo]," +
    "[role=complementary], [role=search], [role=form], [role=region]";

  // ------------------------------------------------------------------ font stack

  function fontInfo(style) {
    const stack = (style.fontFamily || "")
      .split(",")
      .map((f) => f.trim().replace(/^["']|["']$/g, ""))
      .filter(Boolean);
    if (!stack.length) return null;
    const requested = stack[0];
    const size = num(style.fontSize) || 16;
    // ponytail: no browser API exposes the actually-rendered family. document.fonts.check
    // catches the case that matters — a webfont that never loaded — and calls everything
    // else loaded. Upgrade path is a canvas width probe per family if it proves wrong.
    let rendered = requested;
    let fallbackUsed = false;
    try {
      if (!document.fonts.check(`${style.fontWeight || 400} ${size}px "${requested}"`)) {
        fallbackUsed = true;
        rendered = stack.find((f) => document.fonts.check(`${size}px "${f}"`)) || stack[stack.length - 1];
      }
    } catch (e) {
      /* document.fonts unavailable — leave the optimistic answer */
    }
    return { requested, rendered, fallbackUsed };
  }

  // ----------------------------------------------------------------------- walk

  const CLICKABLE_TAGS = new Set(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "LABEL"]);
  const origin = location.origin;
  const scrollX = window.scrollX;
  const scrollY = window.scrollY;

  const nodes = [];
  const ids = new Map();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT, {
    acceptNode: (el) => (SKIP.has(el.tagName) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  nodes.push(document.body);
  while (walker.nextNode()) nodes.push(walker.currentNode);

  const kept = [];
  for (const el of nodes) {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const hasBox = rect.width > 0 || rect.height > 0;
    const rendered = style.display !== "none" && style.visibility !== "hidden";
    const intent = CLICKABLE_TAGS.has(el.tagName) || el.hasAttribute("role");
    if (!hasBox && !intent) continue;
    if (!rendered && !intent) continue;
    if (kept.length >= options.maxElements) break;
    ids.set(el, `el_${String(kept.length + 1).padStart(5, "0")}`);
    kept.push({ el, style, rect });
  }

  function selectorFor(el) {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && n.tagName !== "HTML"; n = n.parentElement) {
      let part = n.tagName.toLowerCase();
      const classes = Array.from(n.classList).filter((c) => !/\d{4,}|^css-|^sc-/.test(c));
      if (classes.length) part += "." + classes.slice(0, 3).join(".");
      const parent = n.parentElement;
      if (parent) {
        const twins = Array.from(parent.children).filter((c) => c.tagName === n.tagName);
        if (twins.length > 1) part += `:nth-of-type(${twins.indexOf(n) + 1})`;
      }
      parts.unshift(part);
      if (parts.length >= 6) break;
    }
    return parts.join(" > ");
  }

  function ownText(el) {
    let out = "";
    for (const child of el.childNodes) {
      if (child.nodeType === 3) out += child.nodeValue;
    }
    return norm(out);
  }

  // nearestHeading, in one document-order pass: an element inherits the last heading
  // among its *own* preceding siblings, else whatever its parent inherited. Descending
  // into siblings would give a call-to-action the heading of the card above it, which is
  // both wrong and unstable — and this feeds elementStableKey.
  const isHeading = (el) => /^H[1-6]$/.test(el.tagName);
  const headingOf = new Map();
  const lastSiblingHeading = new Map();
  const records = [];
  const domNodes = [];

  for (const { el, style, rect } of kept) {
    const id = ids.get(el);
    const role = computedRole(el);
    const parent = el.parentElement;
    const inherited = lastSiblingHeading.get(parent) ?? headingOf.get(parent) ?? null;
    const nearestHeading = isHeading(el) ? norm(el.textContent).slice(0, 120) : inherited;
    headingOf.set(el, nearestHeading);
    if (isHeading(el)) lastSiblingHeading.set(parent, nearestHeading);

    const bg = resolvedBackground(el);
    const own = ownText(el);
    const fg = parseColour(style.color);
    const isFlex = style.display === "flex" || style.display === "inline-flex";

    const landmarkEl = el.closest(LANDMARKS);
    const landmark = landmarkEl
      ? (landmarkEl.getAttribute("role") || landmarkEl.tagName.toLowerCase())
      : null;

    const record = {
      id,
      stableKey: "",
      selector: selectorFor(el),
      tag: el.tagName.toLowerCase(),
      role,
      classes: Array.from(el.classList),
      htmlId: el.id || null,
      testId:
        el.getAttribute("data-testid") ||
        el.getAttribute("data-cy") ||
        el.getAttribute("data-test") ||
        null,
      text: own.slice(0, 400),
      textLength: own.length,
      textFull: norm(el.innerText || el.textContent).slice(0, 400),
      box: {
        x: r2(rect.x + scrollX), y: r2(rect.y + scrollY), w: r2(rect.width), h: r2(rect.height),
      },
      boxViewport: { x: r2(rect.x), y: r2(rect.y), w: r2(rect.width), h: r2(rect.height) },
      scrollW: el.scrollWidth,
      scrollH: el.scrollHeight,
      visible:
        style.display !== "none" &&
        style.visibility !== "hidden" &&
        parseFloat(style.opacity) > 0 &&
        rect.width > 0 &&
        rect.height > 0,
      occludedBy: null,
      clickable:
        CLICKABLE_TAGS.has(el.tagName) ||
        role === "button" ||
        role === "link" ||
        el.hasAttribute("onclick") ||
        style.cursor === "pointer",
      focusable: el.tabIndex >= 0,
      tabIndex: el.tabIndex,
      styles: {
        color: style.color,
        backgroundColor: style.backgroundColor,
        fontFamily: style.fontFamily,
        fontSize: num(style.fontSize),
        fontWeight: parseInt(style.fontWeight, 10) || 400,
        lineHeight: style.lineHeight === "normal" ? null : num(style.lineHeight),
        letterSpacing: style.letterSpacing === "normal" ? 0 : num(style.letterSpacing),
        textTransform: style.textTransform,
        textAlign: style.textAlign,
        textOverflow: style.textOverflow,
        scrollMarginTop: num(style.scrollMarginTop),
        opacity: num(style.opacity),
        marginTop: num(style.marginTop),
        marginRight: num(style.marginRight),
        marginBottom: num(style.marginBottom),
        marginLeft: num(style.marginLeft),
        paddingTop: num(style.paddingTop),
        paddingRight: num(style.paddingRight),
        paddingBottom: num(style.paddingBottom),
        paddingLeft: num(style.paddingLeft),
        borderRadius: [
          num(style.borderTopLeftRadius), num(style.borderTopRightRadius),
          num(style.borderBottomRightRadius), num(style.borderBottomLeftRadius),
        ],
        borderWidth: [
          num(style.borderTopWidth), num(style.borderRightWidth),
          num(style.borderBottomWidth), num(style.borderLeftWidth),
        ],
        borderColor: style.borderTopColor,
        boxShadow: style.boxShadow,
        display: style.display,
        flexDirection: isFlex ? style.flexDirection : null,
        gap: style.gap === "normal" ? 0 : num(style.rowGap || style.gap),
        position: style.position,
        zIndex: style.zIndex,
        overflow: style.overflow,
      },
      resolvedBackground: toCss(bg),
      contrast: own && fg ? contrastRatio(over(fg, bg), bg) : null,
      font: own ? fontInfo(style) : null,
      image: null,
      link: null,
      parentId: ids.get(el.parentElement) || null,
      childIds: Array.from(el.children).map((c) => ids.get(c)).filter(Boolean),
      domDepth: (() => {
        let d = 0;
        for (let n = el; n; n = n.parentElement) d++;
        return d;
      })(),
      nearestHeading,
      nearestLandmark: landmark,
    };

    if (el.tagName === "IMG") {
      record.image = {
        src: el.currentSrc || el.src || "",
        naturalW: el.naturalWidth || 0,
        naturalH: el.naturalHeight || 0,
        renderedW: r2(rect.width),
        renderedH: r2(rect.height),
        bytes: null,
        format: null,
        loaded: el.complete && el.naturalWidth > 0,
        alt: el.getAttribute("alt"),
        loading: el.getAttribute("loading"),
      };
    } else if (style.backgroundImage && style.backgroundImage.startsWith("url(")) {
      const src = style.backgroundImage.slice(4, -1).replace(/^["']|["']$/g, "");
      record.image = {
        src, naturalW: 0, naturalH: 0, renderedW: r2(rect.width), renderedH: r2(rect.height),
        bytes: null, format: null, loaded: true, alt: null, loading: null,
      };
    }

    if (el.tagName === "FORM") {
      record.form = {
        action: el.getAttribute("action"),
        method: (el.getAttribute("method") || "get").toLowerCase(),
        name: el.getAttribute("name"),
        enctype: el.getAttribute("enctype"),
        noValidate: el.hasAttribute("novalidate"),
      };
    }

    // Field *contracts*, never field contents: a password field's value is a credential
    // and has no business in an artifact (CLAUDE.md).
    if (["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) {
      const owner = el.form ? ids.get(el.form) : null;
      const labelled = el.labels && el.labels.length ? norm(el.labels[0].textContent) : null;
      record.field = {
        type: el.tagName === "INPUT" ? (el.type || "text").toLowerCase() : el.tagName.toLowerCase(),
        name: el.getAttribute("name"),
        required: el.required === true,
        disabled: el.disabled === true,
        readOnly: el.readOnly === true,
        placeholder: el.getAttribute("placeholder"),
        pattern: el.getAttribute("pattern"),
        minLength: el.getAttribute("minlength") ? parseInt(el.getAttribute("minlength"), 10) : null,
        maxLength: el.getAttribute("maxlength") ? parseInt(el.getAttribute("maxlength"), 10) : null,
        min: el.getAttribute("min"),
        max: el.getAttribute("max"),
        step: el.getAttribute("step"),
        autocomplete: el.getAttribute("autocomplete"),
        accept: el.getAttribute("accept"),
        multiple: el.multiple === true,
        options: el.tagName === "SELECT"
          ? Array.from(el.options).slice(0, 40).map((o) => norm(o.textContent))
          : [],
        labelledBy: labelled ? labelled.slice(0, 120) : null,
        formElementId: owner || null,
      };
    }

    if (el.tagName === "A" && el.hasAttribute("href")) {
      const raw = el.getAttribute("href");
      record.link = {
        href: raw,
        resolved: el.href,
        target: el.getAttribute("target"),
        rel: el.getAttribute("rel"),
        external: Boolean(el.href) && !el.href.startsWith(origin) && /^https?:/.test(el.href),
      };
    }

    records.push(record);
    domNodes.push(el);
  }

  window.__bureauNodes = domNodes;
  return records;
}
