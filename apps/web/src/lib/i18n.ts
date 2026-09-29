import { DEFAULT_LANGUAGE, isLanguageCode, type LanguageCode } from './languages'

/**
 * The interface's translation.
 *
 * Written out in full rather than loaded from a service, for two reasons: a
 * kiosk has no reliable network, and a translation of a warning that decides
 * whether a reader trusts a passage should be reviewable in the repository
 * rather than arriving from somewhere at runtime.
 *
 * The Hindi and Marathi strings are translations of the interface only. The
 * archive's own text is never translated; see languages.ts for why.
 */
export const STRINGS = {
  en: {
    'site.name': 'Dr. B. R. Ambedkar',
    'site.subtitle': 'Digital Heritage Archive',
    'nav.home': 'Home',
    'nav.explore': 'Explore',
    'nav.ai': 'AI Research',
    'nav.manuscripts': 'Manuscripts',
    'nav.timeline': 'Timeline',
    'nav.graph': 'Knowledge Graph',
    'nav.media': 'Media',
    'nav.stories': 'Stories',
    'nav.digitize': 'Digitise',

    'verification.verified': 'Checked against the original',
    'verification.secondary': 'Unverified secondary text',
    'verification.image': 'Unverified image text (OCR)',
    'verification.translation': 'Machine translation — not a source of record',
    'verification.pending': "Awaiting an archivist's check",
    'verification.unknown': 'Verification status unknown',
    'verification.compactVerified': 'Verified',
    'verification.compactUnverified': 'Unverified',
    'verification.notQuotable': 'Unverified — not quotable',
    'verification.unverifiedText': 'Unverified text',

    'page.unavailable': 'Page information unavailable in indexed source.',
    'date.notRecorded': 'Date not recorded in the indexed source',
    'corpus.heading': 'Read the provenance before you rely on a passage.',
    'corpus.body':
      'Every text in this archive was imported from a third-party dataset and has not been checked against the archival original. Passages are therefore shown as summaries, never as quotations, and every citation links to the source you would need in order to check the wording yourself.',
    'corpus.originals': 'Each record records which archive holds the original.',

    'offline.heading': 'Offline.',
    'offline.body':
      'You are reading a copy stored on this device. It may not be the archive’s current state, and records not yet opened are not available.',

    'language.label': 'Interface language',
    'language.corpusNote':
      'The archive’s text is shown in the language it was transcribed in, and is not translated.',

    'corpus.inspection': 'The records are marked as official transcriptions, but inspection shows modern summaries written in the third person.',
    'corpus.archives': '{host} and the Government of India hold the originals; each record records which one it cites.',
    'provenance.summaryNotQuotation': 'The passage below is a modern summary held in this archive. It is not a transcription, and it is not presented as a quotation.',
    'source.noneRecorded': 'no source link recorded',
    'verification.titleVerified': 'Verified against the original',
    'verification.titleSecondary': 'Unverified secondary text',
    'verification.titleImage': 'Unverified image text (OCR)',
    'verification.titleTranslation': 'Machine translation — not a source of record',
    'verification.titlePending': 'Awaiting an archivist’s check',
    'verification.titleUnknown': 'Verification status unknown',
    'page.cited': 'Page {number}',
    'page.unavailable.title': 'This text is not from a page you can check here',
    'common.loading': 'Loading…',
    'common.retry': 'Try again',
    'common.error': 'Could not reach the archive.',
  },

  hi: {
    'site.name': 'डॉ. भीमराव अम्बेकर',
    'site.subtitle': 'डिजिटल विरासत संग्रह',
    'nav.home': 'मुखपृष्ठ',
    'nav.explore': 'अन्वेषण',
    'nav.ai': 'एआई शोध',
    'nav.manuscripts': 'पांडुलिपियाँ',
    'nav.timeline': 'समयरेखा',
    'nav.graph': 'ज्ञान ग्राफ',
    'nav.media': 'माध्यम',
    'nav.stories': 'कथाएँ',
    'nav.digitize': 'डिजिटाइज़',

    'verification.verified': 'मूल के साथ सत्यापित',
    'verification.secondary': 'असत्यापित द्वितीयक पाठ',
    'verification.image': 'असत्यापित छवि पाठ (ओसीआर)',
    'verification.translation': 'मशीन अनुवाद — अभिलेख का स्रोत नहीं',
    'verification.pending': 'पुर्तागार की जाँच की प्रतीक्षा में',
    'verification.unknown': 'सत्यापन की स्थिति अज्ञात',
    'verification.compactVerified': 'सत्यापित',
    'verification.compactUnverified': 'असत्यापित',
    'verification.notQuotable': 'असत्यापित — उद्धरण योग्य नहीं',
    'verification.unverifiedText': 'असत्यापित पाठ',

    'page.unavailable': 'अनुक्रमित स्रोत में पृष्ठ की जानकारी उपलब्ध नहीं है।',
    'date.notRecorded': 'अनुक्रमित स्रोत में तिथि दर्ज नहीं है',
    'corpus.heading': 'किसी अनुच्छेद पर निर्भर रहने से पहले उसका स्रोत पढ़ें।',
    'corpus.body':
      'इस संग्रह का हर पाठ किसी तृतीय पक्ष के डेटासेट से आयातित किया गया है और मूल पुरातात्विक प्रति के साथ सत्यापित नहीं किया गया है। इसलिए अनुच्छेद सारांश के रूप में ही दिखाए जाते हैं, उद्धरण के रूप में कभी नहीं, और हर संदर्भ उस स्रोत से जुड़ा है जिसकी आपको वाक्यांश की स्वयं पुष्टि करने के लिए आवश्यकता होगी।',
    'corpus.originals': 'प्रत्येक अभिलेख में यह दर्ज है कि मूल पाठ किस अभिलेखागार में है।',

    'offline.heading': 'ऑफ़लाइन।',
    'offline.body':
      'आप इस उपकरण में संग्रहीत प्रति पढ़ रहे हैं। यह संग्रह की वर्तमान स्थिति नहीं हो सकती, और जो अभिलेख पहले नहीं खोले गए हैं वे उपलब्ध नहीं हैं।',

    'language.label': 'इंटरफ़ेस की भाषा',
    'language.corpusNote':
      'संग्रह का पाठ उसी भाषा में दिखाया जाता है जिस भाषा में उसका प्रतिलेखन हुआ है, और उसका अनुवाद नहीं किया जाता।',

    'corpus.inspection': 'अभिलेखों को आधिकारिक प्रतिलेखन के रूप में अंकित किया गया है, पर निरीक्षण से पता चलता है कि ये तीसरे पुरुष में लिखे गए आधुनिक सारांश हैं।',
    'corpus.archives': '{host} और भारत सरकार के पास मूल प्रतियाँ हैं; प्रत्येक अभिलेख में दर्ज है कि वह किसका संदर्भ देता है।',
    'provenance.summaryNotQuotation': 'नीचे दिया गया अनुच्छेद इस संग्रह में रखा एक आधुनिक सारांश है। यह प्रतिलेखन नहीं है, और इसे उद्धरण के रूप में नहीं प्रस्तुत किया गया है।',
    'source.noneRecorded': 'कोई स्रोत लिंक दर्ज नहीं',
    'verification.titleVerified': 'मूल के विरुद्ध सत्यापित',
    'verification.titleSecondary': 'असत्यापित द्वितीयक पाठ',
    'verification.titleImage': 'असत्यापित छवि पाठ (ओसीआर)',
    'verification.titleTranslation': 'मशीन अनुवाद — अभिलेख का स्रोत नहीं',
    'verification.titlePending': 'पुर्तागार की जाँच की प्रतीक्षा में',
    'verification.titleUnknown': 'सत्यापन की स्थिति अज्ञात',
    'page.cited': 'पृष्ठ {number}',
    'page.unavailable.title': 'यह पाठ ऐसे पृष्ठ से नहीं है जिसकी जाँच यहाँ की जा सके',
    'common.loading': 'लोड हो रहा है…',
    'common.retry': 'फिर से प्रयास करें',
    'common.error': 'संग्रह तक नहीं पहुँच सके।',
  },

  mr: {
    'site.name': 'डॉ. भीमराव आंबेडकर',
    'site.subtitle': 'डिजिटल वारसा संग्रह',
    'nav.home': 'मुख्यपृष्ठ',
    'nav.explore': 'शोध',
    'nav.ai': 'एआय संशोधन',
    'nav.manuscripts': 'हस्तलिखित',
    'nav.timeline': 'कालरेषा',
    'nav.graph': 'ज्ञानसंबंधी आलेख',
    'nav.media': 'माध्यमे',
    'nav.stories': 'कथा',
    'nav.digitize': 'डिजिटाइझ',

    'verification.verified': 'मूळाशी सत्यापित',
    'verification.secondary': 'असत्यापित द्वितीयक मजकूर',
    'verification.image': 'असत्यापित प्रतिमंजाकृत मजकूर (ओसीआर)',
    'verification.translation': 'यंत्र अनुवाद — मूळनिधीचा स्रोत नाही',
    'verification.pending': 'पुर्तागाराच्या तपासणीची प्रतीक्षा',
    'verification.unknown': 'सत्यापन स्थिती अज्ञात',
    'verification.compactVerified': 'सत्यापित',
    'verification.compactUnverified': 'असत्यापित',
    'verification.notQuotable': 'असत्यापित — उद्धरणायोग्य नाही',
    'verification.unverifiedText': 'असत्यापित मजकूर',

    'page.unavailable': 'नोंदवलेल्या स्रोतात पानाची माहिती उपलब्ध नाही.',
    'date.notRecorded': 'नोंदवलेल्या स्रोतात तारीख नोंदवलेली नाही',
    'corpus.heading': 'कोणत्याही मजकुरावर अवलंबून राहण्यापूर्वी त्याचा स्रोत वाचा.',
    'corpus.body':
      'या संग्रहातील प्रत्येक मजकूर तिसऱ्या पक्षाच्या डेटासेटमधून आणला गेला आहे आणि मूळ पुरातात्विक प्रतीसोबत सत्यापित केलेला नाही. म्हणून मजकूर सारांशरूपानेच दाखवला जातो, कधीही उद्धरणरूपाने नाही, आणि प्रत्येक संदर्भ त्या स्रोताशी जोडलेला असतो ज्याची तुम्हाला शब्दांची स्वतः खात्री करण्यासाठी आवश्यकता असेल.',
    'corpus.originals': 'प्रत्येक नोंदीत हे नोंदवलेले असते की मूळ मजकूर कोणत्या अभिलेखागारात आहे.',

    'offline.heading': 'ऑफलाइन.',
    'offline.body':
      'तुम्ही या उपकरणावर साठवलेली प्रत वाचत आहात. ही संग्रहाची सध्याची स्थिती असू शकत नाही, आणि आधी उघडलेल्या नाहीत अशा नोंदी उपलब्ध नाहीत.',

    'language.label': 'इंटरफेसची भाषा',
    'language.corpusNote':
      'संग्रहाचा मजकूर ज्या भाषेत त्याची नोंदवणी झाली आहे त्याच भाषेत दाखवला जातो, आणि त्याचा अनुवाद केला जात नाही.',

    'corpus.inspection': 'नोंदी अधिकृत प्रतलेखन म्हणून नोंदवलेल्या आहेत, पर तपासणीत असे दिसून येते की या तृतीय पुरुषात लिहिलेल्या आधुनिक सारांश आहेत.',
    'corpus.archives': '{host} आणि भारत सरकाराकडे मूळ प्रती आहेत; प्रत्येक नोंदीत नोंदवलेले असते की ती कोणाचा संदर्भ देते.',
    'provenance.summaryNotQuotation': 'खालील मजकूर या संग्रहात साठवलेला एक आधुनिक सारांश आहे. हे प्रतलेखन नाही, आणि उद्धरण म्हणून सादर केलेले नाही.',
    'source.noneRecorded': 'कोणताही स्रोत दुवा नोंदवलेला नाही',
    'verification.titleVerified': 'मूळाशी सत्यापित',
    'verification.titleSecondary': 'असत्यापित द्वितीयक मजकूर',
    'verification.titleImage': 'असत्यापित प्रतिमंजाकृत मजकूर (ओसीआर)',
    'verification.titleTranslation': 'यंत्र अनुवाद — मूळनिधीचा स्रोत नाही',
    'verification.titlePending': 'पुर्तागाराच्या तपासणीची प्रतीक्षा',
    'verification.titleUnknown': 'सत्यापन स्थिती अज्ञात',
    'page.cited': 'पान {number}',
    'page.unavailable.title': 'हा मजकूर अशा पानातून नाही ज्याची तपासणी येथे करता येईल',
    'common.loading': 'लोड होत आहे…',
    'common.retry': 'पुन्हा प्रयत्न करा',
    'common.error': 'संग्रहाशी संपर्क साधता आला नाही.',
  },
} as const

export type StringKey = keyof typeof STRINGS.en

/**
 * Fill in `{name}` placeholders.
 *
 * Translations are free to move a placeholder: some of these languages put the
 * object before the verb where English puts it after, and a fixed sentence order
 * would produce something that is grammatically wrong in the reader's language
 * even when every word is correct.
 */
export function interpolate(text: string, values: Record<string, string | number>): string {
  return text.replace(/\{(\w+)\}/g, (match, name: string) =>
    name in values ? String(values[name]) : match,
  )
}

const STORAGE_KEY = 'dha.interface-language'

/**
 * Look up a string.
 *
 * A missing translation returns the English text and says so, rather than
 * showing a key name. A reader seeing `verification.secondary` would be looking
 * at an interface that had stopped telling them whether text was verified.
 */
export function translate(key: StringKey, language: LanguageCode = DEFAULT_LANGUAGE): string {
  return resolveString(key, STRINGS[language] as Partial<Record<StringKey, string>>, STRINGS.en)
}

/**
 * Look a key up in one table, falling back to another.
 *
 * Split out from `translate` so the fallback can be tested by removing an entry
 * from a copy of the table. A test that asserted the fallback by calling
 * `translate` on a complete table would pass without ever reaching it.
 */
export function resolveString(
  key: StringKey,
  table: Partial<Record<StringKey, string>>,
  fallback: Record<StringKey, string>,
): string {
  return table[key] ?? fallback[key] ?? key
}

/** Read the stored preference, ignoring anything unrecognised. */
export function readLanguage(): LanguageCode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    return isLanguageCode(stored) ? stored : DEFAULT_LANGUAGE
  } catch {
    return DEFAULT_LANGUAGE
  }
}

export function storeLanguage(language: LanguageCode): void {
  try {
    localStorage.setItem(STORAGE_KEY, language)
    // The whole document is marked, so assistive technology and any printed
    // page read in the right language rather than the interface's.
    document.documentElement.lang = language
  } catch {
    // A browser that refuses to store the choice still works in English.
  }
}
