// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts for the approvals extension: English fallback content for registered topics.

export const HELP = {
  "approvals.view": {
    "title": "Approvals",
    "hint": "Review secret-like content, external requests, and restricted bundle loads here.",
    "more": [
      "Sends containing keys or tokens to external models or executors, and external requests such as OSV.dev, are stopped.",
      "Approve only here, with a browser session and a one-time code, so another process cannot approve for you.",
      "A process running as you could read browser cookies or console output. Open this page yourself, and reject if unsure."
    ]
  },
  "approvals.refresh": {
    "title": "Refresh approvals",
    "hint": "Read pending requests from the local server again.",
    "more": [
      "This does not approve, reject, or use up any request.",
      "Use it to check for a new request or see whether another request changed."
    ]
  },
  "approvals.approve": {
    "title": "Approve this send",
    "hint": "Request a one-time code and authorize only this exact request.",
    "more": [
      "The code works once for the shown content and destination, and expires after 120 seconds.",
      "The decision is not remembered and does not authorize later sends or text changes."
    ]
  },
  "approvals.memory_trust": {
    "title": "Approve this note",
    "hint": "The note becomes trusted, as if you had confirmed it yourself.",
    "more": [
      "A one-time code applies only to this note and its current text.",
      "If you did not write it, click Reject. The note stays untrusted."
    ]
  },
  "approvals.memory_clash": {
    "title": "Approve the new note",
    "hint": "The new note becomes trusted. Your note stays in Pending so you can decide whether to delete it.",
    "more": [
      "Until you decide, both notes are treated as trusted. This click does not delete anything.",
      "A one-time code applies only to this note and its current text.",
      "If the new note is not true, click Reject. It stays untrusted and is not used."
    ]
  },
  "approvals.memory_delete": {
    "title": "Approve deletion",
    "hint": "The note will be deleted and cannot be restored.",
    "more": [
      "A one-time code applies only to this note.",
      "If you did not request deletion, click Reject. The note stays as it is."
    ]
  },
  "approvals.reject": {
    "title": "Reject a request",
    "hint": "Deny this request so no restricted content is sent or loaded.",
    "more": [
      "The request is closed as rejected and cannot be reused.",
      "The browser session also records the decision in the history."
    ]
  },
  "approvals.retry_code": {
    "title": "Try again",
    "hint": "Request a new one-time code if the previous one expired.",
    "more": [
      "Each attempt creates a different code that is valid for 120 seconds.",
      "If the problem continues after restarting the server, reload from “multiagent gui”."
    ]
  },
  "approvals.osv_allow": {
    "title": "Allow the request",
    "hint": "Allow a package lookup at api.osv.dev to scan this bundle.",
    "more": [
      "Only package names and versions declared in the listed manifests are sent.",
      "A one-time code allows this once for this fingerprint."
    ]
  },
  "approvals.osv_deny": {
    "title": "Deny the request",
    "hint": "Deny the request to api.osv.dev. The bundle will remain unanalyzed.",
    "more": [
      "SkillSpector will not run, and package data will not be sent over the internet.",
      "Loading the bundle will require manual confirmation because it was not analyzed."
    ]
  },
  "approvals.bundle_allow": {
    "title": "Load this bundle",
    "hint": "Allow this exact version of the bundle to load after you review the result.",
    "more": [
      "This applies only to this fingerprint. Any file change will require another review.",
      "It uses a one-time code from the session open in this tab."
    ]
  },
  "approvals.bundle_risk": {
    "title": "I accept the risks",
    "hint": "Confirm that you reviewed the result before loading this bundle.",
    "more": [
      "SkillSpector may recommend against installing it or may not have analyzed it fully.",
      "The server requires this confirmation for every load that needs your approval."
    ]
  },
  "approvals.bundle_deny": {
    "title": "Do not load",
    "hint": "Keep this bundle fingerprint blocked from loading.",
    "more": [
      "The bundle's contracts will not be imported or run.",
      "The decision is recorded in the approvals history."
    ]
  }
};
