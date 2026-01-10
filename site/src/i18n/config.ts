export const languages = {
  fr: "Français",
  en: "English",
} as const;

export type Language = keyof typeof languages;

export const defaultLang: Language = "fr";

export const supportedLanguages = Object.keys(languages) as Language[];

/**
 * URL patterns for localized routes.
 * French uses French terms (SEO-optimized for French discovery).
 * English uses English terms.
 */
export const routes = {
  fr: {
    home: "/fr",
    professionals: "/fr/professionnels",
    families: "/fr/familles",
    about: "/fr/a-propos",
    search: "/fr/recherche",
    articles: "/fr/articles",
    categories: "/fr/categories",
  },
  en: {
    home: "/en",
    professionals: "/en/professionals",
    families: "/en/families",
    about: "/en/about",
    search: "/en/search",
    articles: "/en/articles",
    categories: "/en/categories",
  },
} as const;

/**
 * Get the alternate language for hreflang tags
 */
export function getAlternateLang(lang: Language): Language {
  return lang === "fr" ? "en" : "fr";
}

/**
 * Check if a language code is supported
 */
export function isValidLang(lang: string): lang is Language {
  return lang in languages;
}
