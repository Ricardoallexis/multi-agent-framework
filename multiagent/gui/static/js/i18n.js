// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Synchronous UI translations. Dictionaries are plain ES modules grouped by view.
import mainEn from "../i18n/en/main.js";
import mainEs from "../i18n/es/main.js";
import customizeEn from "../i18n/en/customize.js";
import customizeEs from "../i18n/es/customize.js";
import commandPaletteEn from "../i18n/en/command-palette.js";
import commandPaletteEs from "../i18n/es/command-palette.js";
import shellEn from "../i18n/en/shell.js";
import shellEs from "../i18n/es/shell.js";
import newSessionEn from "../i18n/en/new-session.js";
import newSessionEs from "../i18n/es/new-session.js";
import createRunEn from "../i18n/en/create-run.js";
import createRunEs from "../i18n/es/create-run.js";
import modelPickerEn from "../i18n/en/model-picker.js";
import modelPickerEs from "../i18n/es/model-picker.js";
import runsEn from "../i18n/en/runs.js";
import runsEs from "../i18n/es/runs.js";
import threadEn from "../i18n/en/thread.js";
import threadEs from "../i18n/es/thread.js";
import commonEn from "../i18n/en/common.js";
import commonEs from "../i18n/es/common.js";
import teamsEn from "../i18n/en/teams.js";
import teamsEs from "../i18n/es/teams.js";
import plansEn from "../i18n/en/plans.js";
import plansEs from "../i18n/es/plans.js";
import workMapEn from "../i18n/en/work-map.js";
import workMapEs from "../i18n/es/work-map.js";
import agentTreeEn from "../i18n/en/agent-tree.js";
import agentTreeEs from "../i18n/es/agent-tree.js";
import startEn from "../i18n/en/start.js";
import startEs from "../i18n/es/start.js";
import sessionsEn from "../i18n/en/sessions.js";
import sessionsEs from "../i18n/es/sessions.js";
import packagesEn from "../i18n/en/packages.js";
import packagesEs from "../i18n/es/packages.js";
import pendingEn from "../i18n/en/pending.js";
import pendingEs from "../i18n/es/pending.js";
import taskWaitingEn from "../i18n/en/task-waiting.js";
import taskWaitingEs from "../i18n/es/task-waiting.js";
import usageEn from "../i18n/en/usage.js";
import usageEs from "../i18n/es/usage.js";
import evaluationEn from "../i18n/en/evaluation.js";
import evaluationEs from "../i18n/es/evaluation.js";
import panelEn from "../i18n/en/panel.js";
import panelEs from "../i18n/es/panel.js";
import subagentsEn from "../i18n/en/subagents.js";
import subagentsEs from "../i18n/es/subagents.js";
import openInAppEn from "../i18n/en/open-in-app.js";
import openInAppEs from "../i18n/es/open-in-app.js";
import officeLevelQuestionEn from "../i18n/en/office-level-question.js";
import officeLevelQuestionEs from "../i18n/es/office-level-question.js";
import quickGuideEn from "../i18n/en/quick-guide.js";
import quickGuideEs from "../i18n/es/quick-guide.js";
import { readLanguagePreference, writeLanguagePreference } from "./shell-state.js";
const dictionaries = {
  en: { ...mainEn, ...customizeEn, ...commandPaletteEn, ...shellEn, ...newSessionEn, ...createRunEn, ...modelPickerEn, ...runsEn, ...threadEn, ...commonEn, ...teamsEn, ...plansEn, ...workMapEn, ...agentTreeEn, ...startEn, ...sessionsEn, ...packagesEn, ...pendingEn, ...taskWaitingEn, ...usageEn, ...evaluationEn, ...panelEn, ...subagentsEn, ...openInAppEn, ...officeLevelQuestionEn, ...quickGuideEn },
  es: { ...mainEs, ...customizeEs, ...commandPaletteEs, ...shellEs, ...newSessionEs, ...createRunEs, ...modelPickerEs, ...runsEs, ...threadEs, ...commonEs, ...teamsEs, ...plansEs, ...workMapEs, ...agentTreeEs, ...startEs, ...sessionsEs, ...packagesEs, ...pendingEs, ...taskWaitingEs, ...usageEs, ...evaluationEs, ...panelEs, ...subagentsEs, ...openInAppEs, ...officeLevelQuestionEs, ...quickGuideEs },
};
const listeners = new Set();

export function languageChoice() {
  const choice = sessionChoice || readLanguagePreference();
  return choice === "en" || choice === "es" ? choice : "auto";
}

export function currentLanguage() {
  const choice = languageChoice();
  if (choice !== "auto") return choice;
  return typeof navigator !== "undefined" && /^es(?:-|$)/i.test(navigator.language || "") ? "es" : "en";
}

function updateDocumentLanguage() {
  if (typeof document !== "undefined") document.documentElement.lang = currentLanguage();
}

export function setLanguage(id) {
  const choice = id === "en" || id === "es" ? id : "auto";
  writeLanguagePreference(choice);
  sessionChoice = choice;
  updateDocumentLanguage();
  for (const listener of [...listeners]) listener(currentLanguage());
}

let sessionChoice = null;
export function onLanguageChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function registerTranslations(language, entries) {
  if (language !== "en" && language !== "es") return;
  Object.assign(dictionaries[language], entries);
}

export function t(key, data = {}) {
  const language = currentLanguage();
  const value = dictionaries[language][key] ?? dictionaries.en[key] ?? key;
  return typeof value === "string"
    ? value.replace(/\{([A-Za-z_][A-Za-z_0-9]*)\}/g, (match, name) => data[name] == null ? match : String(data[name]))
    : value;
}

updateDocumentLanguage();
