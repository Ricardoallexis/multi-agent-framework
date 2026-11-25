// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Synchronous UI translations. Dictionaries are plain ES modules grouped by view.
import mainEn from "../i18n/en/main.js";
import mainEs from "../i18n/es/main.js";
import customizeEn from "../i18n/en/customize.js";
import customizeEs from "../i18n/es/customize.js";
import paletteEn from "../i18n/en/command-palette.js";
import paletteEs from "../i18n/es/command-palette.js";
import { readLanguagePreference, writeLanguagePreference } from "./shell-state.js";
const dictionaries = { en: { ...mainEn, ...customizeEn, ...paletteEn }, es: { ...mainEs, ...customizeEs, ...paletteEs } };
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
