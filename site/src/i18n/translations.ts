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
      articles: "Articles",
      professionals: "Professionnels",
      families: "Familles",
      about: "À propos",
      search: "Rechercher",
      categories: "Catégories",
      glossary: "Glossaire",
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
      // Hero
      heroBadge: "52 articles traduits · Sources évaluées par les pairs",
      heroTitle: "La bibliothèque de recherche sur le PDA",
      heroSubtitle:
        "Littérature scientifique sur le Profil d'Évitement Pathologique des Demandes, traduite en français pour les cliniciens et familles francophones.",
      heroDisclaimer: "Traductions d'articles publiés — pas de contenu original. Sources toujours citées.",

      // Featured section
      featuredTitle: "Pour commencer",
      featuredIntro:
        "Le PDA est un profil du spectre autistique caractérisé par un évitement intense des demandes du quotidien. Peu connu en France, il est souvent confondu avec un trouble oppositionnel ou une difficulté éducative. Les approches classiques aggravent fréquemment la situation.",
      featuredSubtitle: "Ces articles offrent une introduction aux enjeux clés.",

      // Featured cards
      featuredWhatIs: "Qu'est-ce que le PDA ?",
      featuredWhatIsDesc: "Définition, caractéristiques principales et place dans le spectre autistique.",
      featuredRecognize: "Reconnaître le profil PDA",
      featuredRecognizeDesc: "Signes cliniques, outils de dépistage et diagnostics différentiels.",
      featuredApproaches: "Approches adaptées",
      featuredApproachesDesc: "Stratégies éducatives et thérapeutiques spécifiques au profil PDA.",

      // Categories section
      browseByTheme: "Explorer par catégorie",
      browseByThemeSubtitle: "Tous les articles, organisés par thème clinique.",
      viewAllArticles: "Voir tous les articles",
      articleCount: "{count} articles",

      // Recent section
      latestTranslations: "Ajouts récents",
      latestSubtitle: "Dernières traductions publiées.",
      readArticle: "Lire",

      // Mission section
      missionTitle: "Pourquoi ce site existe",
      missionText:
        "Il existe un seul article évalué par les pairs en français sur le PDA. Plus de 50 études ont été publiées en anglais — inaccessibles aux cliniciens francophones.",
      missionCta: "En savoir plus",

      // Footer
      footerTagline: "Traductions de la littérature scientifique sur le Profil d'Évitement Pathologique des Demandes.",
      footerDisclaimer: "Ce site traduit des articles publiés. Les sources originales sont toujours citées et liées.",
      footerNavigation: "Navigation",
      footerAbout: "À propos",
      footerMethodology: "Méthodologie",
      footerContact: "Contact",
      footerGlossary: "Glossaire",

      // Legacy (keep for compatibility)
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
      temoignages: "Témoignages",
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
      temoignages: "Récits de personnes PDA et de leurs proches",
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
      articles: "Articles",
      professionals: "Professionals",
      families: "Families",
      about: "About",
      search: "Search",
      categories: "Categories",
      glossary: "Glossary",
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
      // Hero
      heroBadge: "52 articles translated · Peer-reviewed sources",
      heroTitle: "The PDA Research Library",
      heroSubtitle:
        "Scientific literature on Pathological Demand Avoidance, translated into French for French-speaking clinicians and families.",
      heroDisclaimer: "Translations of published articles — no original content. Sources always cited.",

      // Featured section
      featuredTitle: "Getting started",
      featuredIntro:
        "PDA is an autism spectrum profile characterized by intense avoidance of everyday demands. Little known in France, it is often mistaken for oppositional defiant disorder or a parenting issue. Standard approaches frequently make things worse.",
      featuredSubtitle: "These articles offer an introduction to the key issues.",

      // Featured cards
      featuredWhatIs: "What is PDA?",
      featuredWhatIsDesc: "Definition, key characteristics, and place within the autism spectrum.",
      featuredRecognize: "Recognizing the PDA profile",
      featuredRecognizeDesc: "Clinical signs, screening tools, and differential diagnosis.",
      featuredApproaches: "Adapted approaches",
      featuredApproachesDesc: "Educational and therapeutic strategies specific to the PDA profile.",

      // Categories section
      browseByTheme: "Browse by category",
      browseByThemeSubtitle: "All articles, organized by clinical topic.",
      viewAllArticles: "View all articles",
      articleCount: "{count} articles",

      // Recent section
      latestTranslations: "Recent additions",
      latestSubtitle: "Latest translations published.",
      readArticle: "Read",

      // Mission section
      missionTitle: "Why this site exists",
      missionText:
        "There is only one peer-reviewed article on PDA in French. Over 50 studies have been published in English — inaccessible to French-speaking clinicians.",
      missionCta: "Learn more",

      // Footer
      footerTagline: "Translations of scientific literature on Pathological Demand Avoidance.",
      footerDisclaimer: "This site translates published articles. Original sources are always cited and linked.",
      footerNavigation: "Navigation",
      footerAbout: "About",
      footerMethodology: "Methodology",
      footerContact: "Contact",
      footerGlossary: "Glossary",

      // Legacy (keep for compatibility)
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
      temoignages: "Lived Experience",
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
      temoignages: "Accounts from PDA people and their families",
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
