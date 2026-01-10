import { defineMiddleware } from "astro:middleware";
import { defaultLang, supportedLanguages, type Language } from "./i18n/config";

const LANG_COOKIE_NAME = "pda_lang";
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365; // 1 year

/**
 * Parse Accept-Language header to get preferred language
 */
function getPreferredLanguageFromHeader(
  acceptLanguage: string | null
): Language | null {
  if (!acceptLanguage) return null;

  // Parse Accept-Language header (e.g., "fr-FR,fr;q=0.9,en;q=0.8")
  const languages = acceptLanguage
    .split(",")
    .map((lang) => {
      const [code, qValue] = lang.trim().split(";q=");
      return {
        code: code.split("-")[0].toLowerCase(), // Get base language code
        q: qValue ? parseFloat(qValue) : 1.0,
      };
    })
    .sort((a, b) => b.q - a.q);

  // Find first supported language
  for (const { code } of languages) {
    if (supportedLanguages.includes(code as Language)) {
      return code as Language;
    }
  }

  return null;
}

/**
 * Get language from cookie
 */
function getLanguageFromCookie(cookieHeader: string | null): Language | null {
  if (!cookieHeader) return null;

  const cookies = Object.fromEntries(
    cookieHeader.split(";").map((cookie) => {
      const [key, value] = cookie.trim().split("=");
      return [key, value];
    })
  );

  const langCookie = cookies[LANG_COOKIE_NAME];
  if (langCookie && supportedLanguages.includes(langCookie as Language)) {
    return langCookie as Language;
  }

  return null;
}

export const onRequest = defineMiddleware(async (context, next) => {
  const { pathname } = context.url;

  // Only redirect from root path
  if (pathname === "/") {
    // Priority: 1. Cookie, 2. Accept-Language header, 3. Default (French)
    const cookieHeader = context.request.headers.get("cookie");
    const acceptLanguage = context.request.headers.get("accept-language");

    const preferredLang =
      getLanguageFromCookie(cookieHeader) ||
      getPreferredLanguageFromHeader(acceptLanguage) ||
      defaultLang;

    // Redirect to preferred language
    return context.redirect(`/${preferredLang}`, 302);
  }

  // For language-prefixed paths, set/update the language cookie
  const langMatch = pathname.match(/^\/(fr|en)(?:\/|$)/);
  if (langMatch) {
    const response = await next();

    // Clone response to add cookie header
    const newResponse = new Response(response.body, response);
    newResponse.headers.set(
      "Set-Cookie",
      `${LANG_COOKIE_NAME}=${langMatch[1]}; Path=/; Max-Age=${COOKIE_MAX_AGE}; SameSite=Lax`
    );

    return newResponse;
  }

  return next();
});
