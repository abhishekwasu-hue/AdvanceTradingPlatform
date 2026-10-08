/**
 * P0.9: the ONLY frontend file allowed to hold Devanagari (scripts/check-devanagari.mjs). The dashboard is English;
 * the strategy interview shows a small muted Marathi line under its own English prompts. The questions and their
 * answer options come from the server with both languages; these are the interview's few client-side prompts.
 */
export const INTERVIEW_MR: Record<string, string> = {
  otherAmount: "दुसरी रक्कम",
  typeSymbol: "symbol लिहा",
  welcomeBack: "मागची उत्तरे वापरायची का?",
  readMarket: "Market चा data वाचून templates दाखवा",
  chooseTemplate: "Template निवडा - ही शिफारस नाही",
  start: "मुलाखत सुरू करा",
};
