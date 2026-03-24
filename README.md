# tapasmitra.com — Mitra Legal Services

Official website of **Tapas Kumar Mitra**, Advocate and Retired Judicial Officer (Erstwhile Additional District & Sessions Judge, Fast Track Court).

**Live site:** [tapasmitra.com](https://tapasmitra.com)

---

## About

This is a bespoke, single-page static website built for Mitra Legal Services. It showcases the judicial career, areas of practice, judgements, legal resources, and contact information of Tapas Kumar Mitra.

**Tagline:** Route of Law

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Markup | HTML5 (semantic) |
| Styling | CSS3 + Bootstrap 5.3 |
| Scripting | Vanilla JavaScript (ES6+) |
| Icons | Bootstrap Icons 1.11.3 |
| Fonts | Google Fonts (Playfair Display, Cormorant Garamond, DM Sans) |
| Hosting | GitHub Pages |
| Domain | Custom domain via CNAME (`tapasmitra.com`) |

---

## File Structure

```
tapasmitra.github.io/
├── index.html              ← Single-page site (all sections)
├── 404.html                ← Branded 404 error page
├── CNAME                   ← Custom domain: tapasmitra.com
├── .nojekyll               ← Skips Jekyll processing on GitHub Pages
├── README.md               ← This file
├── favicon.ico             ← Root favicon
└── assets/
    ├── css/
    │   └── style.css       ← All custom CSS (Bootstrap overrides + layout)
    ├── js/
    │   └── main.js         ← All custom JavaScript
    ├── img/
    │   ├── logo.svg        ← Mitra Legal Services circular seal logo
    │   ├── hero-bg-pattern.svg  ← Subtle laurel branch background pattern
    │   ├── favicon-16x16.png    ← Replace with actual favicon
    │   ├── favicon-32x32.png    ← Replace with actual favicon
    │   ├── apple-touch-icon.png ← Replace with actual icon (180×180)
    │   └── og-image.jpg         ← Open Graph social share image (1200×630)
    └── docs/
        └── (reserved for PDF judgements/orders)
```

---

## Sections

1. **Hero** — Name, title, tagline, CTA buttons
2. **About** — Bio and judicial service stats
3. **Judicial Career** — Animated timeline of 8 postings (2005–2022)
4. **Practice Areas** — 6 service cards
5. **Judgements** — Civil & Criminal judgements + Notable Orders
6. **Resources** — SVSPA study material + placeholder cards
7. **Contact** — Contact info + form (Formspree integration)

---

## Contact Form Setup (Formspree)

The contact form is pre-wired for [Formspree](https://formspree.io). To activate:

1. Sign up at [formspree.io](https://formspree.io)
2. Create a new form pointing to `tapaskalyani.mitra@gmail.com`
3. In `index.html`, find the `<form>` element and replace `YOUR_FORM_ID`:
   ```html
   <form action="https://formspree.io/f/YOUR_FORM_ID" ...>
   ```
4. Commit and push — the form will then submit directly to email.

Until configured, the form falls back to opening the user's default email client.

---

## Images to Replace

The following images need to be provided by the client and placed in `assets/img/`:

| File | Purpose | Dimensions |
|------|---------|-----------|
| `logo.svg` | Main circular seal logo | SVG (vector) — current file is auto-generated placeholder |
| `favicon.ico` | Root favicon | 32×32 |
| `favicon-16x16.png` | 16px favicon | 16×16 |
| `favicon-32x32.png` | 32px favicon | 32×32 |
| `apple-touch-icon.png` | iOS home screen icon | 180×180 |
| `og-image.jpg` | Social media share preview | 1200×630 |

---

## Contact

- **Phone:** +91 94337 34997 / +91 89102 08262
- **Email:** tapaskalyani.mitra@gmail.com
- **Location:** Bardhaman, West Bengal, India
