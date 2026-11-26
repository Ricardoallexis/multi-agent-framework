// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts for Sessions, Usage, Evaluation, Context, subagents, Pending, Packages, and Start.
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

export const HELP = {
  "sessions.list": {
    "title": "All sessions",
    "hint": "Claude, Codex, and Copilot sessions and framework runs, newest first.",
    "more": [
      "This view only reads sessions. It does not change or delete them.",
      "Only folders allowed by gui_session_roots are read.",
      "Open a row to see the full conversation and follow new messages."
    ]
  },
  "sessions.refresh": {
    "title": "Refresh the list",
    "hint": "Read sessions from disk again.",
    "more": [
      "Use this if a session you just started is missing.",
      "Your filters stay in place."
    ]
  },
  "sessions.source": {
    "title": "Session source",
    "hint": "The tool that created the session.",
    "more": [
      "Claude, Codex, and Copilot are the tools where you opened a session. Framework means a run in this program.",
      "All does not filter by source.",
      "The filter applies as soon as you choose an option."
    ]
  },
  "sessions.surface": {
    "title": "Surface",
    "hint": "Where it was used: a desktop app or terminal.",
    "more": [
      "Desktop means the desktop app, CLI means the terminal, and Framework means runs in this program.",
      "Unknown means the source did not say.",
      "This works with the other filters."
    ]
  },
  "sessions.text": {
    "title": "Title contains",
    "hint": "Find a word in the session title.",
    "more": [
      "This searches titles only, not the conversation, and ignores letter case.",
      "It applies shortly after you stop typing.",
      "This works with the other filters."
    ]
  },
  "sessions.cwd": {
    "title": "Folder",
    "hint": "Show sessions in this folder or its subfolders.",
    "more": [
      "Enter a full path, such as C:\\projects\\web.",
      "You still only see folders allowed by gui_session_roots.",
      "Framework runs have no folder, so this filter hides them."
    ]
  },
  "sessions.since": {
    "title": "Updated since",
    "hint": "Show sessions active on or after this day.",
    "more": [
      "This uses the last update date, not the creation date.",
      "The whole chosen day is included, starting at midnight in your local time.",
      "You can use either date alone or both dates."
    ]
  },
  "sessions.until": {
    "title": "Updated until",
    "hint": "Show sessions active up to this day.",
    "more": [
      "This uses the last update date, not the creation date.",
      "The whole chosen day is included, through 11:59 p.m. in your local time.",
      "You can use either date alone or both dates."
    ]
  },
  "sessions.subagents": {
    "title": "Include subagents",
    "hint": "Also show sessions started by another session.",
    "more": [
      "A subagent is a helper session created for a specific task.",
      "They are hidden by default to keep the list short; you can still reach them from the main session.",
      "Each subagent row has a Subagent label."
    ]
  },
  "sessions.search": {
    "title": "Search",
    "hint": "Apply the filters now.",
    "more": [
      "Menus and dates apply when selected; text applies shortly after typing.",
      "This button applies everything now and reloads the list."
    ]
  },
  "sessions.clear": {
    "title": "Clear filters",
    "hint": "Remove all filters.",
    "more": [
      "All allowed sessions appear again.",
      "This does not change the subagent checkbox."
    ]
  },
  "sessions.warnings": {
    "title": "Source warnings",
    "hint": "Minor problems while reading a session source.",
    "more": [
      "For example, a folder could not be read or damaged lines were skipped.",
      "Other sessions still appear normally.",
      "If a session is missing, check here first."
    ]
  },
  "sessions.more": {
    "title": "Load more",
    "hint": "Get the next group of sessions.",
    "more": [
      "Sessions are read in groups of 50 so the list opens quickly.",
      "This button appears only when more sessions exist.",
      "The filters stay in place."
    ]
  },
  "sessions.retry": {
    "title": "Retry",
    "hint": "Try reading the list again.",
    "more": [
      "Use this after a read fails, such as when the server did not respond.",
      "Your filters stay in place."
    ]
  },
  "sessions.empty": {
    "title": "No visible sessions",
    "hint": "Why the list may be empty.",
    "more": [
      "The viewer shows only sessions in folders allowed by gui_session_roots (GUI_SESSION_ROOTS).",
      "If a session is missing, check that its work folder is under an allowed path and restart the interface after changing the setting.",
      "If filters are on, try clearing them."
    ]
  },
  "sessions.roots": {
    "title": "Allowed folders",
    "hint": "For privacy, the viewer shows nothing until you allow folders.",
    "more": [
      "gui_session_roots (GUI_SESSION_ROOTS) lists the work folders whose sessions can be viewed.",
      "Separate paths with a semicolon or use a JSON list. You can also put the same line in the local .env file.",
      "Restart the interface after changing it."
    ]
  },
  "session.back": {
    "title": "Back to list",
    "hint": "Close this session and return to the list.",
    "more": [
      "Your filters and loaded pages are kept.",
      "You can also press Esc."
    ]
  },
  "session.run_link": {
    "title": "View the run",
    "hint": "Open this framework run in Runs.",
    "more": [
      "There you can approve, request changes, or cancel. Those actions are not available here.",
      "This screen shows only the conversation and run events."
    ]
  },
  "session.refresh": {
    "title": "Refresh details",
    "hint": "Read the session summary again.",
    "more": [
      "This updates dates, models, tokens, and the subagent count.",
      "The conversation is updated by live follow instead."
    ]
  },
  "session.facts": {
    "title": "Session details",
    "hint": "Where, when, and with which models the session ran.",
    "more": [
      "The folder is the work directory. Framework runs have none.",
      "Dates belong to the session. The last update also appears as relative time, such as 5 minutes ago.",
      "Tokens are units used for model input, output, and cache. If the source omits them, they show as unknown, never zero.",
      "The ID is used by this viewer to link the session."
    ]
  },
  "session.live": {
    "title": "Live follow",
    "hint": "Whether new session content arrives without reloading.",
    "more": [
      "It starts after you load the session to the end with Load more.",
      "New messages then appear at the end as they arrive.",
      "If the connection drops, it retries automatically."
    ]
  },
  "session.pause": {
    "title": "Pause or resume live follow",
    "hint": "Stop or resume new messages.",
    "more": [
      "Pausing closes the live connection. New messages appear after you resume.",
      "Resuming continues from the last received message without losing any."
    ]
  },
  "session.retry": {
    "title": "Retry live follow",
    "hint": "Reconnect live follow.",
    "more": [
      "This appears when live follow ends or fails.",
      "It continues from the last received message."
    ]
  },
  "session.warnings": {
    "title": "Read warnings",
    "hint": "Parts of this session the viewer could not read properly.",
    "more": [
      "For example, damaged or oversized lines were skipped, or a changed file was read again.",
      "The rest of the session appears normally.",
      "With more than three warnings, this section starts closed."
    ]
  },
  "session.items": {
    "title": "Session content",
    "hint": "The conversation in the order the tool saved it.",
    "more": [
      "It includes user and assistant messages, tool calls and results, system notices, and subagents.",
      "Everything is plain text. Long results start collapsed.",
      "New messages appear at the end while live follow is active."
    ]
  },
  "session.reasoning": {
    "title": "Reasoning omitted",
    "hint": "Marks where the model reasoned, without showing that text.",
    "more": [
      "Models may reason before answering. Internal reasoning may be saved in a session, but this viewer never displays it.",
      "You see only the notice. This is not a read error."
    ]
  },
  "session.more": {
    "title": "Load more",
    "hint": "Get the next part of the conversation.",
    "more": [
      "Long sessions are read in groups of 200 items so they open quickly.",
      "Live follow starts when you reach the end."
    ]
  },
  "session.items_retry": {
    "title": "Retry reading",
    "hint": "Try reading the content again.",
    "more": [
      "Reading resumes where it failed.",
      "If it keeps failing, check that the session file still exists and the server is running."
    ]
  },
  "usage.section": {
    "title": "Usage",
    "hint": "See measured use of plans, models, and framework calls.",
    "more": [
      "Figures come from local files and framework telemetry.",
      "Sources may cover different time windows or have no data."
    ]
  },
  "usage.plans": {
    "title": "Plans by app",
    "hint": "Show percentages and reset times only when the source knows them.",
    "more": [
      "A missing percentage means the source does not provide enough data to calculate it.",
      "After a window resets, current usage is unknown rather than zero."
    ]
  },
  "usage.global": {
    "title": "Overall usage",
    "hint": "Group tokens by app, model, level, and agent type.",
    "more": [
      "Rows combine events made available by each app.",
      "Missing data stays unknown and is not counted as zero."
    ]
  },
  "usage.period": {
    "title": "Usage period",
    "hint": "Switch between today and the last seven days.",
    "more": [
      "Dates use your browser's time zone.",
      "Rows and totals use the selected period."
    ]
  },
  "usage.costs": {
    "title": "API and budget",
    "hint": "Show estimated costs with their evidence and separate unknown amounts.",
    "more": [
      "Cost uses observed tokens and available prices.",
      "It is not a billed amount. A call without a current price stays unknown.",
      "Below, you set a monthly budget for each API provider: an empty amount means no budget and 0 means do not spend."
    ]
  },
  "usage.refresh": {
    "title": "Refresh usage",
    "hint": "Get plans, tokens, and costs again.",
    "more": [
      "The screen keeps the last response if a source fails.",
      "Apps may update their files at different times."
    ]
  },
  "usage.view": {
    "title": "Usage sections",
    "hint": "Choose plans, overall usage, or API costs.",
    "more": [
      "Each section shows what its local source can measure.",
      "In the advanced mode, use the arrow keys to move among these tabs."
    ]
  },
  "usage.plan_notes": {
    "title": "Source notes",
    "hint": "Explain what each app could measure.",
    "more": [
      "Notes describe limits or warnings in local measurement.",
      "Missing data does not mean zero usage."
    ]
  },
  "usage.cost_reasons": {
    "title": "Unknown amounts",
    "hint": "Explain why some calls have no calculated cost.",
    "more": [
      "A current price or token count may be missing, or the step may be human work.",
      "These records are separate from the subtotal with known prices."
    ]
  },
  "usage.cost_evidence": {
    "title": "Price evidence",
    "hint": "Show where the estimated price came from.",
    "more": [
      "Evidence identifies the model, currency, and price source.",
      "The subtotal is still an estimate, not a billed amount."
    ]
  },
  "evaluation.section": {
    "title": "Evaluation",
    "hint": "See evaluation sets and whether their latest run got worse.",
    "more": [
      "This view only reads saved runs. It does not run sets or set baselines.",
      "Run sets and set baselines from the command line or API."
    ]
  },
  "evaluation.refresh": {
    "title": "Refresh evaluation",
    "hint": "Read the sets or the open set again.",
    "more": [
      "The screen keeps the last response if reading fails.",
      "New runs appear after refreshing."
    ]
  },
  "evaluation.sets": {
    "title": "Evaluation sets",
    "hint": "Show each set's latest score and status against its baseline.",
    "more": [
      "Mock means it used simulated responses; Live means a real model.",
      "Regression means the latest run got worse against the baseline. No baseline means there is nothing to compare yet.",
      "Open a set by name to see its runs and comparison."
    ]
  },
  "evaluation.runs": {
    "title": "Saved runs",
    "hint": "List this set's runs, newest first.",
    "more": [
      "Each row shows the set version, score, who saved it, and whether it is the baseline.",
      "Only runs saved in the system database appear."
    ]
  },
  "evaluation.compare": {
    "title": "Compare with baseline",
    "hint": "Compare the latest run with the baseline run.",
    "more": [
      "A case regresses when it passed before and now fails, or its score drops beyond the threshold.",
      "A regression is a comparison result, not an error."
    ]
  },
  "evaluation.cases": {
    "title": "Compared cases",
    "hint": "Mark each case as regression, improvement, changed, or unchanged.",
    "more": [
      "Changed means its checks or fingerprint differ without crossing the threshold.",
      "Cases present in only one run appear separately and do not count as regressions."
    ]
  },
  "evaluation.fingerprint": {
    "title": "Fingerprint changes",
    "hint": "Show what differs between the baseline and latest run.",
    "more": [
      "The fingerprint covers the agent or prompt, workflow, models and providers, and the set itself.",
      "It helps explain what changed when a case gets worse."
    ]
  },
  "subagents.panel": {
    "title": "Session subagents",
    "hint": "Helpers that work or worked for this session.",
    "more": [
      "This includes Claude subagents, Codex child threads, Copilot subagents, and steps of a framework run.",
      "Each shows its name or description, model, status, last activity, and tokens used.",
      "The list updates automatically. If live connection fails, it reloads every ten seconds."
    ]
  },
  "subagents.refresh": {
    "title": "Refresh subagents",
    "hint": "Read the subagent list now.",
    "more": [
      "The list updates itself, but this button reads it immediately.",
      "It only reads app files and changes nothing in those apps."
    ]
  },
  "subagents.retry": {
    "title": "Retry reading subagents",
    "hint": "Request the list again after a failure.",
    "more": [
      "This button appears only when the subagent list could not be read.",
      "If it keeps failing, check that the local server is running and the session is in an allowed folder."
    ]
  },
  "subagents.back": {
    "title": "Back to main session",
    "hint": "Return to the session that started this subagent.",
    "more": [
      "While you view a subagent's conversation, the list still shows its siblings.",
      "This link returns to the main conversation."
    ]
  },
  "subagents.tabs": {
    "title": "Active and finished subagents",
    "hint": "Switch between running and finished helpers.",
    "more": [
      "Active means working, running a test, or active within the past hour.",
      "Finished means ended, inactive for over an hour, or in an unknown state.",
      "Use arrow keys to move between tabs."
    ]
  },
  "subagents.item": {
    "title": "Open a subagent",
    "hint": "Select one to see its full conversation.",
    "more": [
      "The conversation opens on this screen while the list stays visible.",
      "Framework run steps open their containing run.",
      "Subagents without their own conversation are not links. Their activity appears as notices in the session."
    ]
  },
  "subagents.more": {
    "title": "Show more subagents",
    "hint": "Show the rest of the tab or collapse it again.",
    "more": [
      "The list starts with the ten most recently active.",
      "The button shows the rest, then can return to the first ten."
    ]
  },
  "panel.changes": {
    "title": "Changes",
    "hint": "Files changed by the open session or run.",
    "more": [
      "For sessions, these come from tools that write or edit files. For runs, they come from result files.",
      "Each file shows how often it changed and, when available, lines added and removed.",
      "Many apps do not report line counts. In that case only edit counts appear.",
      "Only paths appear, never message content."
    ]
  },
  "panel.tokens": {
    "title": "Tokens",
    "hint": "How much text models processed and its estimated cost.",
    "more": [
      "A token is a piece of text a model reads or writes. More tokens can raise the cost.",
      "The orchestrator is the open session; workers are its subagents or run steps.",
      "Unknown data is shown as unknown, never zero. Cost is estimated only with a current catalog price.",
      "Apps do not report prices, so their session cost remains unknown."
    ]
  },
  "panel.sources": {
    "title": "Sources",
    "hint": "Files, pages, and references used by the session or run.",
    "more": [
      "For a session: its work folder, files and folders it read, web pages it opened, and counts of searches and attachments.",
      "For a run: the workflow, prompts, and step contracts, and input names, never their values.",
      "Web addresses omit everything after a question mark, where secrets may appear."
    ]
  },
  "panel.retry": {
    "title": "Retry reading",
    "hint": "Request this section's data again after a failure.",
    "more": [
      "This button appears only when its data could not be read.",
      "If it keeps failing, check that the local server is running."
    ]
  },
  "panel.more": {
    "title": "Show more or less",
    "hint": "Show the rest of the list or collapse it again.",
    "more": [
      "The list starts with the eight most recent items.",
      "The button shows the rest, then can return to the first eight."
    ]
  },
  "panel.by_model": {
    "title": "Tokens by model",
    "hint": "Group tokens by the models that worked.",
    "more": [
      "Workers are grouped by model and level, with their calls or subagents.",
      "Those without token data are counted separately, not as zero."
    ]
  },
  "panel.cost_evidence": {
    "title": "Price evidence",
    "hint": "The token price used to estimate cost.",
    "more": [
      "For each model, see input and output prices, source, validity dates, and provenance.",
      "A call without a current price has unknown cost and is not added.",
      "Cost is an estimate using tokens and catalog prices, not a billed amount."
    ]
  },
  "pending.toggle": {
    "title": "Pending",
    "hint": "Open items awaiting a decision or review.",
    "more": [
      "The count includes runs, proposals, and tasks waiting for a person.",
      "It updates automatically while the tab is open."
    ]
  },
  "pending.open": {
    "title": "Open pending item",
    "hint": "Open the view related to this item.",
    "more": [
      "The link only changes the view.",
      "It does not approve, reject, or start work."
    ]
  },
  "pending.refresh": {
    "title": "Refresh pending items",
    "hint": "Read the list from the local server again.",
    "more": [
      "This request only reads data.",
      "It uses the local GUI token."
    ]
  },
  "pending.notifications": {
    "title": "Browser notifications",
    "hint": "Turn notifications for new pending items on or off in this browser.",
    "more": [
      "Permission is requested only when you press this button.",
      "Notifications are optional and appear only if the browser grants permission."
    ]
  },
  "pending.tabs": {
    "title": "Pending sections",
    "hint": "All combines the list; each tab opens a view awaiting a decision.",
    "more": [
      "Arrow keys, Home, and End change tabs. The page address shows which one is open.",
      "The count by a tab shows its pending items. Document alerts have no count."
    ]
  },
  "pending.background": {
    "title": "In the background",
    "hint": "Show active and queued runs.",
    "more": [
      "The list updates with Pending.",
      "Each link opens that run's thread."
    ]
  },
  "pending.cancel": {
    "title": "Request cancellation",
    "hint": "Prepare a message to cancel this run.",
    "more": [
      "Confirmation is required before saving it.",
      "This is available only when the availability policy is on."
    ]
  },
  "pending.cancel_confirm": {
    "title": "Confirm cancellation",
    "hint": "Save the cancellation message.",
    "more": [
      "The run stops when its worker reads the message.",
      "This does not approve any pending review."
    ]
  },
  "pending.cancel_back": {
    "title": "Back to background",
    "hint": "Close confirmation without sending.",
    "more": [
      "The run stays in its current state.",
      "You can open its thread from the link."
    ]
  },
  "packages.install_zip": {
    "title": "Install a package",
    "hint": "Choose a .zip package file. In advanced mode, you can also give a folder.",
    "more": [
      "The file is checked before installation. Escaping paths, links, and too many files are rejected.",
      "Up to 25 MB compressed, and 50 MB or 2,000 files when extracted.",
      "A package with code requires advanced mode and asks for approval on first load."
    ]
  },
  "packages.example": {
    "title": "Example package",
    "hint": "Register the example package included with the product without copying it.",
    "more": [
      "Use it to try the product with a ready made team. It appears only when example folders are present.",
      "Removing it does not delete anything from the product folder."
    ]
  },
  "packages.trust_functions": {
    "title": "Trust package functions",
    "hint": "Allow this package's function code to run on your computer.",
    "more": [
      "This applies only to packages with their own functions and requires advanced mode.",
      "Trust is tied to this copy. If the package changes or is reinstalled, you must trust it again.",
      "If unsure, leave this off. The package still installs, but its functions will not load."
    ]
  },
  "packages.remove": {
    "title": "Remove a package",
    "hint": "Take it off the list. Its data and runs are kept.",
    "more": [
      "Removing does not delete the installed copy or saved data.",
      "You cannot remove a package while its runs are active.",
      "You can install another package with the same name later."
    ]
  },
  "packages.degraded": {
    "title": "Package changed or missing",
    "hint": "Its copy no longer matches the installed one, so it will not load.",
    "more": [
      "Reinstall from a .zip file to replace the copy with a new version.",
      "Or remove it from the list. Its data will be kept."
    ]
  },
  "start.steps": {
    "title": "Start steps",
    "hint": "Only unfinished steps appear, in order, each with a button.",
    "more": [
      "The product checks what is actually present on your computer. You do not mark steps by hand.",
      "A finished step does not return. Connect and Activate open Apps and agents; nothing is activated here.",
      "This list updates every few seconds."
    ]
  },
  "start.times": {
    "title": "Start times",
    "hint": "The time of each completed step and minutes since start.",
    "more": [
      "Copy puts this text on the clipboard exactly as shown. It does not copy settings.",
      "The person watching the trial excludes download time using their own timer."
    ]
  },
  "start.real_models": {
    "title": "Switch to real models",
    "hint": "Change the model setting to real. It applies when you open Start again.",
    "more": [
      "Real models may cost money or use your quota. This screen asks you to confirm first.",
      "This appears after you try Mock and see the document in the thread.",
      "You can return to Mock here at any time."
    ]
  },
  "start.office_example": {
    "title": "Install the example office",
    "hint": "Available only in a project copy.",
    "more": [
      "The office is copied when you press the button, not when the program opens.",
      "Its teams remain proposed. None is approved."
    ]
  },
  "start.office_import": {
    "title": "Import an office",
    "hint": "Open Offices to import your own office.",
    "more": [
      "In an installed framework copy, import your own office from a file.",
      "Its teams remain proposed until you approve them."
    ]
  }
};
