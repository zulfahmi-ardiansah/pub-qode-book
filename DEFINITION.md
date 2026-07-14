# Definition

Two classification systems. Both strict hierarchies encoded in fixed-width codes; each level narrows the scope of the level above. Each digit pair/digit repeats the parent code and appends a child selector.

---

## UNSPSC

United Nations Standard Products and Services Code. Classifies products and services. Four numeric levels under a single alphabetic root. Encoded as 8-digit code `SSFFCCXX` — each pair of digits addresses one level. A pair of `00` = level not specified.

| Level | Code mask | Digit positions | Scope |
|-------|-----------|-----------------|-------|
| Root | Single alphabet | — | All goods and services globally. Conceptual root, no digits. |
| Segment | `xx000000` | 1–2 | Broadest category. An overall industry or function. |
| Family | `xxxx0000` | 3–4 | Grouping of interrelated categories within a Segment. |
| Class | `xxxxxx00` | 5–6 | Items sharing a common use or function within a Family. |
| Commodity | `xxxxxxxx` | 7–8 | Most specific level. A unique item. |

---

## KBLI

Klasifikasi Baku Lapangan Usaha Indonesia (Indonesian Standard Industrial Classification). Classifies economic activities and business sectors of a company. Five levels under a single alphabetic Kategori. Numeric code grows one digit per level (2→3→4→5). At each level leading digits repeat the parent; final digit selects the child. Max 9 children per parent.

| Level | Code mask | Digits | Scope |
|-------|-----------|--------|-------|
| Kategori | Single alphabet | — | Top-level economic activity category. |
| Golongan Pokok | `xx` | 2 | Main group within a Kategori, split by characteristics. |
| Golongan | `xxx` | 3 | Group within a main group. Digits: 2 parent + 1 activity selector. |
| Sub Golongan | `xxxx` | 4 | Subgroup within a group. Digits: 3 parent + 1 activity selector. |
| Kelompok | `xxxxx` | 5 | Most homogeneous activity within a subgroup. Digits: 4 parent + 1 selector. |

---

## Mapping

UNSPSC = what product/service is transacted. KBLI = what business activity the company performs. Independent taxonomies, no fixed 1:1 correspondence. Relationship is many-to-many: one KBLI activity → many UNSPSC commodities; one commodity → many activities. Resolve at most specific level available (Commodity ↔ Kelompok).
