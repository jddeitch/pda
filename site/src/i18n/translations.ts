import type { Language } from "./config";

/**
 * UI translations for the site.
 * Organized by context to support different information hierarchies.
 */
export const translations = {
  fr: {
    // Site identity
    site: {
      title: "PDA France",
      tagline: "Ressources francophones sur l'Évitement Pathologique des Demandes",
      description:
        "Littérature scientifique sur le PDA traduite en français pour les cliniciens et les familles",
    },

    // Navigation
    nav: {
      home: "Accueil",
      professionals: "Professionnels",
      families: "Familles",
      about: "À propos",
      search: "Rechercher",
      categories: "Thèmes",
    },

    // Homepage - ICP cards
    icp: {
      professionals: {
        title: "Professionnels de santé",
        subtitle: "Évaluation, diagnostic, prise en charge",
        cta: "Ressources cliniques",
      },
      families: {
        title: "Familles & PDAers",
        subtitle: "Comprendre, accompagner, expliquer",
        cta: "Ressources pratiques",
      },
    },

    // Homepage sections
    home: {
      heroTitle: "Évitement Pathologique des Demandes",
      heroSubtitle:
        "Un profil comportemental au sein de l'autisme, pratiquement inconnu en France",
      browseByTheme: "Parcourir par thème",
      latestTranslations: "Dernières traductions",
      viewAll: "Voir tout",
    },

    // Article display
    article: {
      summary: "Résumé",
      fullText: "Texte intégral",
      source: "Source",
      authors: "Auteurs",
      year: "Année",
      journal: "Revue",
      keywords: "Mots-clés",
      categories: "Catégories",
      viewOriginal: "Voir l'original",
      keyFindings: "Points clés",
      translationNote: "Traduction française",
    },

    // Classification badges
    badges: {
      peerReviewed: "Évalué par les pairs",
      openAccess: "Accès libre",
      empirical: "Empirique",
      synthesis: "Synthèse",
      theoretical: "Théorique",
      livedExperience: "Expérience vécue",
      academic: "Académique",
      practitioner: "Praticien",
      organization: "Organisation",
      individual: "Individuel",
    },

    // Category names (match taxonomy.yaml)
    categories: {
      fondements: "Fondements",
      evaluation: "Évaluation",
      presentation_clinique: "Présentation clinique",
      etiologie: "Étiologie et mécanismes",
      prise_en_charge: "Prise en charge",
      comorbidites: "Comorbidités",
      trajectoire: "Trajectoire développementale",
    },

    // Category descriptions for cards
    categoryDescriptions: {
      fondements: "Définitions, histoire, conceptualisation du PDA",
      evaluation: "Outils de dépistage, instruments diagnostiques",
      presentation_clinique: "Profils comportementaux, études de cas",
      etiologie: "Bases neurobiologiques, mécanismes anxieux",
      prise_en_charge: "Stratégies d'intervention, approches éducatives",
      comorbidites: "Anxiété, TDAH, conditions associées",
      trajectoire: "Enfants, adolescents, adultes",
    },

    // Professionals landing page
    professionals: {
      title: "PDA pour les professionnels de santé",
      intro:
        "Le PDA (Pathological Demand Avoidance) est un profil comportemental au sein de l'autisme, caractérisé par un évitement anxieux des demandes du quotidien. Pratiquement inconnu en France, il est pourtant bien documenté dans la littérature anglophone.",
      startHere: "Par où commencer",
      essentialReading: "Lectures essentielles",
      steps: {
        understand: "Comprendre le concept",
        recognize: "Reconnaître les signes",
        assess: "Outils d'évaluation",
        intervene: "Stratégies d'intervention",
      },
    },

    // Families landing page
    families: {
      title: "PDA pour les familles",
      intro:
        "Vous pensez que votre enfant présente un profil PDA ? Ou vous avez reçu ce diagnostic et cherchez à mieux comprendre ? Vous êtes au bon endroit.",
      understandPDA: "Comprendre le PDA",
      forProfessionals: "Pour expliquer aux professionnels",
      forProfessionalsSubtitle:
        "Articles à partager avec l'équipe soignante de votre enfant",
    },

    // About page
    about: {
      title: "À propos de ce projet",
      whyThisExists: "Pourquoi ce projet existe",
      methodology: "Méthodologie",
      whoWeAre: "Qui sommes-nous",
    },

    // Search
    search: {
      title: "Rechercher",
      placeholder: "Rechercher dans les articles...",
      noResults: "Aucun résultat",
      resultsFor: "Résultats pour",
    },

    // Common UI
    ui: {
      loading: "Chargement...",
      error: "Erreur",
      backToHome: "Retour à l'accueil",
      readMore: "Lire la suite",
      shareArticle: "Partager",
      printArticle: "Imprimer",
    },

    // Language switcher
    lang: {
      switchTo: "English",
      current: "Français",
    },
  },

  en: {
    // Site identity
    site: {
      title: "PDA France",
      tagline: "French-language resources on Pathological Demand Avoidance",
      description:
        "Scientific literature on PDA translated into French for clinicians and families",
    },

    // Navigation
    nav: {
      home: "Home",
      professionals: "Professionals",
      families: "Families",
      about: "About",
      search: "Search",
      categories: "Topics",
    },

    // Homepage - ICP cards
    icp: {
      professionals: {
        title: "Healthcare Professionals",
        subtitle: "Assessment, diagnosis, management",
        cta: "Clinical resources",
      },
      families: {
        title: "Families & PDAers",
        subtitle: "Understand, support, advocate",
        cta: "Practical resources",
      },
    },

    // Homepage sections
    home: {
      heroTitle: "Pathological Demand Avoidance",
      heroSubtitle: "A behavioral profile within autism, virtually unknown in France",
      browseByTheme: "Browse by topic",
      latestTranslations: "Latest translations",
      viewAll: "View all",
    },

    // Article display
    article: {
      summary: "Summary",
      fullText: "Full text",
      source: "Source",
      authors: "Authors",
      year: "Year",
      journal: "Journal",
      keywords: "Keywords",
      categories: "Categories",
      viewOriginal: "View original",
      keyFindings: "Key findings",
      translationNote: "French translation",
    },

    // Classification badges
    badges: {
      peerReviewed: "Peer reviewed",
      openAccess: "Open access",
      empirical: "Empirical",
      synthesis: "Synthesis",
      theoretical: "Theoretical",
      livedExperience: "Lived experience",
      academic: "Academic",
      practitioner: "Practitioner",
      organization: "Organization",
      individual: "Individual",
    },

    // Category names
    categories: {
      fondements: "Foundations",
      evaluation: "Assessment",
      presentation_clinique: "Clinical Presentation",
      etiologie: "Etiology",
      prise_en_charge: "Management",
      comorbidites: "Comorbidities",
      trajectoire: "Developmental Trajectory",
    },

    // Category descriptions
    categoryDescriptions: {
      fondements: "Core definitions, history, conceptualization",
      evaluation: "Screening tools, diagnostic instruments",
      presentation_clinique: "Behavioral profiles, case studies",
      etiologie: "Neurobiological underpinnings, anxiety mechanisms",
      prise_en_charge: "Intervention strategies, educational approaches",
      comorbidites: "Anxiety, ADHD, overlapping conditions",
      trajectoire: "Children, adolescents, adults",
    },

    // Professionals landing page
    professionals: {
      title: "PDA for Healthcare Professionals",
      intro:
        "PDA (Pathological Demand Avoidance) is a behavioral profile within autism, characterized by anxiety-driven avoidance of everyday demands. Virtually unknown in France, it is well documented in English-language literature.",
      startHere: "Where to start",
      essentialReading: "Essential reading",
      steps: {
        understand: "Understand the concept",
        recognize: "Recognize the signs",
        assess: "Assessment tools",
        intervene: "Intervention strategies",
      },
    },

    // Families landing page
    families: {
      title: "PDA for Families",
      intro:
        "Do you think your child has a PDA profile? Or have you received this diagnosis and want to understand better? You're in the right place.",
      understandPDA: "Understanding PDA",
      forProfessionals: "For explaining to professionals",
      forProfessionalsSubtitle: "Articles to share with your child's care team",
    },

    // About page
    about: {
      title: "About this project",
      whyThisExists: "Why this project exists",
      methodology: "Methodology",
      whoWeAre: "Who we are",
    },

    // Search
    search: {
      title: "Search",
      placeholder: "Search articles...",
      noResults: "No results",
      resultsFor: "Results for",
    },

    // Common UI
    ui: {
      loading: "Loading...",
      error: "Error",
      backToHome: "Back to home",
      readMore: "Read more",
      shareArticle: "Share",
      printArticle: "Print",
    },

    // Language switcher
    lang: {
      switchTo: "Français",
      current: "English",
    },
  },
} as const;

export type Translations = typeof translations;

/**
 * Get translations for a specific language
 */
export function t(lang: Language) {
  return translations[lang];
}

/**
 * Get a specific translation key
 */
export function translate<
  K1 extends keyof Translations["fr"],
  K2 extends keyof Translations["fr"][K1]
>(lang: Language, section: K1, key: K2): Translations["fr"][K1][K2] {
  return translations[lang][section][key];
}
