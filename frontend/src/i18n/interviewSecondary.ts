/**
 * P0.9 / P0.10: the interview namespace - the ONLY frontend file allowed to hold Devanagari (scripts/check-devanagari.mjs
 * checks that every Devanagari line here is a value of INTERVIEW_MR, and that only the interview screens import it).
 * The dashboard is English; the strategy interview shows every English line with a small muted Marathi line under it:
 * intro, questions, answer options, tips, buttons and the template headings. The questions, their options and tips
 * come from the server with both languages; these are the interview's client-side lines.
 */
export const INTERVIEW_MR = {
  // the Start card on the AI Copilot page
  startIntro: "आधी काही प्रश्न - भांडवल, risk, पद्धत, वेळ आणि ध्येय. मग market चा data वाचला जातो (trend, structure, support/resistance) आणि तीन templates त्यांच्या नियम, backtest आणि risk settings सह दाखवले जातात. Template तुम्ही निवडता; ही शिफारस नाही.",
  start: "मुलाखत सुरू करा",
  startOver: "पुन्हा सुरुवात करा",
  // the conversation
  alreadyUnderstood: "तुमच्या message मधून आधीच समजलेले:",
  welcomeBack: "पुन्हा स्वागत! मागच्या वेळची उत्तरे जतन आहेत. ती वापरून थेट templates कडे जायचे का?",
  yesUse: "हो, ती वापरा",
  noAsk: "नको, पुन्हा विचारा",
  forget: "माझी माहिती विसरा",
  usedLastAnswers: "हो, मागची उत्तरे वापरा",
  otherAmount: "दुसरी रक्कम",
  typeSymbol: "symbol लिहा",
  ok: "ठीक",
  back: "मागे",
  question: "प्रश्न",
  of: "पैकी",
  thanks: "धन्यवाद - इतके पुरे. आता market चा data वाचला जाईल (trend, structure, support/resistance, volatility) आणि त्यावर तीन templates तपासले जातील.",
  readMarket: "Market चा data वाचून templates दाखवा",
  onSample: "SAMPLE data वर - आजच्या खऱ्या market साठी वर broker candles निवडा",
  onBroker: "broker candles वर",
  reading: "Market वाचत आहे आणि templates तपासत आहे…",
  // the templates
  chooseTemplate: "तीन templates - निवड तुमची",
  chooseTemplateHint: "Template उघडून त्याचे नियम शब्दांत आणि backtest वाचा. निर्णय तुमचा; ही शिफारस नाही.",
  changedFromFeedback: "तुमच्या प्रतिसादावरून बदलले:",
  chooseThis: "हे निवडा",
  chosen: "निवडले",
  notThis: "हे नको",
  whyNot: "का नको? (एक किंवा जास्त निवडा)",
  showOther: "इतर templates दाखवा",
  openTemplate: "नियम, backtest, risk settings आणि buttons पाहण्यासाठी वर एक template निवडा.",
  // the final buttons
  applyRisk: "Risk settings लागू करा",
  acceptLoss: "प्रत्येक trade मध्ये इतक्या कमाल तोट्याला मी मान्यता देतो/देते",
  deployPaper: "PAPER मध्ये deploy करा",
  openChart: "Chart उघडा",
  askAi: "AI कडून स्वतःचा नियम-संच मागा",
} as const;
