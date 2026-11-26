// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the app shell (top bar, sidebar, Ctrl+K, «Personalizar»), «Nueva sesión» and the run form, and the
// entries the run form builds from data (execution modes, workflow inputs).
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.
import { t } from "./i18n.js";

export const HELP = {
  "create.back": {
    "title": "Back",
    "hint": "Close the form without creating a run.",
    "more": [
      "Return to the screen where you opened the form: the runs list or the new-session chat.",
      "Your form entries are discarded."
    ]
  },
  "create.bundle": {
    "title": "Workflow source",
    "hint": "Choose included workflows or workflows from a bundle.",
    "more": [
      "Included workflows come with the framework.",
      "A bundle is a folder of workflows that you registered when starting the interface with --bundle name=folder.",
      "Changing the source clears the selected workflow and loads the workflows from the new source."
    ]
  },
  "create.workflow": {
    "title": "Workflow",
    "hint": "The workflow to run: a series of steps and their prompts.",
    "more": [
      "When you choose one, its description, steps, and required inputs appear.",
      "Steps run in order, and some may be skipped based on the settings.",
      "You must choose a workflow."
    ]
  },
  "create.steps": {
    "title": "Workflow steps",
    "hint": "The steps this run will follow, in order.",
    "more": [
      "Each step produces a result that the next step can use.",
      "“Skipped by default” means a step runs only when a condition calls for it, such as a web search.",
      "This list is for review only. No step runs until you create the run."
    ]
  },
  "create.project": {
    "title": "Project",
    "hint": "A name to recognize this run in the list.",
    "more": [
      "This is your label and does not change what the workflow does.",
      "Use between 2 and 120 characters.",
      "Use it to group runs for the same work."
    ]
  },
  "create.inputs.required": {
    "title": "Required inputs",
    "hint": "Information the workflow needs.",
    "more": [
      "Each input fills a {name} placeholder in the prompts with your text.",
      "You cannot create the run while a required input is empty. An empty field is marked when you leave it.",
      "Each field shows its description and, when available, an example."
    ]
  },
  "create.inputs.optional": {
    "title": "Optional inputs",
    "hint": "Information you can leave blank.",
    "more": [
      "If you leave an input blank, its placeholder stays empty in the prompt.",
      "The preview lists any inputs you left blank.",
      "Fill them in when you want to give the model more direction."
    ]
  },
  "create.inputs.context": {
    "title": "Optional context",
    "hint": "Extra notes or background that may help but are not required.",
    "more": [
      "These inputs have names ending in _context and are never required.",
      "They are collapsed to keep the form short. Open them only when useful."
    ]
  },
  "create.mode": {
    "title": "How it runs",
    "hint": "Choose whether the framework or a person handles each step.",
    "more": [
      "Automatic: each step uses the model assigned by the workflow, usually on your device unless it needs web search.",
      "Local model: every step uses a model on your device, and nothing is sent to the cloud.",
      "Cloud model: uses an outside provider, may cost money, and cannot be used with sensitive content.",
      "Human guided: the framework does not call a model. Copy the prompt, use it where you like, and paste the answer."
    ]
  },
  "create.operating_mode": {
    "title": "Operating mode",
    "hint": "Optional: ask for reviews between roles or allow role writes.",
    "more": [
      "No mode uses the approved team mode, if there is one, or the usual behavior without a mode.",
      "Supervised asks a person to approve each handoff between roles and each tool write.",
      "Autonomous allows ordinary writes permitted for the role, within the run's limits.",
      "Destructive and external actions, and plan approval, always wait for a person."
    ]
  },
  "create.sensitive": {
    "title": "Sensitive content",
    "hint": "Select this if the text must stay on your device.",
    "more": [
      "Automatic mode then uses local models only.",
      "Cloud model mode rejects the run.",
      "Human-guided mode adds a privacy notice to the prompt you copy."
    ]
  },
  "create.confirm_real": {
    "title": "Confirm real models",
    "hint": "The server is not in Mock mode and may use real models.",
    "more": [
      "Mock mode uses synthetic results at no cost; this run may not.",
      "A run with real models may take time and may cost money, depending on the provider.",
      "Select the box to confirm you understand. You cannot create the run without it."
    ]
  },
  "create.submit": {
    "title": "Create run",
    "hint": "Create the run and add it to the server's queue.",
    "more": [
      "This button is available when the workflow, project, and required inputs are ready.",
      "If it is unavailable, the message above explains what is missing.",
      "Pressing it twice with the same details does not create two runs."
    ]
  },
  "create.cancel": {
    "title": "Cancel",
    "hint": "Discard the form without creating a run.",
    "more": [
      "Return to the runs list.",
      "Your entries are discarded."
    ]
  },

  "preview.panel": {
    "title": "Prompt preview",
    "hint": "See each step's prompt with the text you entered.",
    "more": [
      "This is only a preview. Nothing runs or is saved.",
      "When a run starts, the framework adds response-format instructions to the end of each prompt.",
      "Prompts that need earlier results show those placeholders as empty until the earlier steps run."
    ]
  },
  "preview.copy": {
    "title": "Copy this prompt",
    "hint": "Copy this step's prompt to the clipboard.",
    "more": [
      "Use it to try the prompt in another tool before running it.",
      "If your browser blocks clipboard access, select the text and press Ctrl+C."
    ]
  },

  "shell.menu": {
    "title": "Session menu",
    "hint": "Show or hide the session list on narrow screens.",
    "more": [
      "On narrow screens, the session list is hidden to make room for the content.",
      "This button opens the list as a drawer over the page and closes it again.",
      "The drawer also closes when you press Esc or choose an item."
    ]
  },
  "shell.help_mode": {
    "title": "Help mode",
    "hint": "Show or hide the “?” buttons and hints for each control.",
    "more": [
      "When off, only the “?” buttons on headings and approvals are visible. Screen readers can still access the help.",
      "You can also toggle it with F1.",
      "This browser remembers your choice."
    ]
  },
  "shell.quick_guide": {
    "title": "Quick guide and shortcuts",
    "hint": "Open a short guide to the help and the keyboard shortcuts.",
    "more": [
      "The guide explains how the “?” help works and lists the shortcuts that exist.",
      "It also says how to turn the help back on."
    ]
  },
  "shell.panel": {
    "title": "Context",
    "hint": "Show or hide context for the open session or run.",
    "more": [
      "It starts closed and appears only when a session or run is open.",
      "It groups changes, subagents, tokens, and sources, and shows cards only when they have content.",
      "A dot by the button means there is something to see while the panel is closed.",
      "On narrow screens, the panel appears below the content."
    ]
  },
  "shell.new_session": {
    "title": "New session",
    "hint": "Start new work with a guided chat.",
    "more": [
      "Describe what you need or choose an option. The chat asks for workflow inputs one at a time.",
      "Nothing runs until you select “Launch” in the final summary.",
      "To use the full form instead, select “View as form” in the chat."
    ]
  },
  "chat.welcome": {
    "title": "New-session chat",
    "hint": "Start work here by describing what you need or choosing an option.",
    "more": [
      "The chat suggests workflows, asks for inputs one at a time, and then shows a summary.",
      "A model can read your text and suggest a workflow. If it is a cloud model, the chat asks before sending your text.",
      "If you do not send it, the chat compares your words with workflow names and descriptions.",
      "The run is created only when you select “Launch”."
    ]
  },
  "chat.office": {
    "title": "Office",
    "hint": "Choose an office or one of its area options.",
    "more": [
      "The picker shows installed offices only.",
      "In Single session, options are grouped by area. After you choose one, I will ask for its inputs.",
      "When you ask an office to do something, its coordinator may prepare a plan."
    ]
  },
  "chat.starters": {
    "title": "Ways to start",
    "hint": "Shortcuts to get started without typing.",
    "more": [
      "“Work on a workflow” shows the available workflows.",
      "“Continue a run” lists runs you can pick up.",
      "“Help” explains the chat and its commands."
    ]
  },
  "chat.composer": {
    "title": "Message box",
    "hint": "Type your request or answer here. Press Enter to send it.",
    "more": [
      "Enter sends the message, and Shift+Enter adds a new line.",
      "You can type the commands /workflow, /continuar, and /ayuda.",
      "When a question appears, your text answers the input shown."
    ]
  },
  "chat.send": {
    "title": "Send message",
    "hint": "Send the text in the message box.",
    "more": [
      "This does the same as pressing Enter in the message box.",
      "For an optional question, sending an empty box skips it."
    ]
  },
  "chat.cloud_confirm": {
    "title": "Send to cloud model",
    "hint": "Allow this text to be sent to the named cloud model to suggest a workflow.",
    "more": [
      "Your text and the workflow catalog are sent to the model named in the prompt.",
      "If you choose “Do not send”, the chat compares words without calling the model.",
      "Each model call counts toward usage and follows budget limits."
    ]
  },
  "chat.skip": {
    "title": "Skip question",
    "hint": "Leave this optional input blank and go to the next one.",
    "more": [
      "This option appears only for optional inputs.",
      "Its placeholder stays empty in the prompt.",
      "Select “Edit” in the summary to fill it in later."
    ]
  },
  "chat.back": {
    "title": "Previous question",
    "hint": "Go back without losing your answer.",
    "more": [
      "Your previous answer appears in the box so you can change it.",
      "Going back does not run anything."
    ]
  },
  "chat.restart": {
    "title": "Start over",
    "hint": "Discard this conversation and return to the start.",
    "more": [
      "Your answers so far are discarded.",
      "This does not affect runs you already created."
    ]
  },
  "chat.form": {
    "title": "View as form",
    "hint": "Open the full form with your answers so far.",
    "more": [
      "The form has the same fields, plus advanced settings.",
      "Your chat answers are already filled in.",
      "If you return to the chat, changes made in the form are not copied back."
    ]
  },
  "chat.suggestion": {
    "title": "Choose a workflow",
    "hint": "Select a card to work with that workflow.",
    "more": [
      "Each card shows the workflow name, description, and source.",
      "Choosing one starts the questions. Nothing runs yet.",
      "If none fit, try different words or select “Show all workflows”."
    ]
  },
  "chat.all": {
    "title": "Show all workflows",
    "hint": "Show the full list without filtering by your text.",
    "more": [
      "Use this when none of the suggestions fit.",
      "The same cards appear, sorted by source and name."
    ]
  },
  "chat.choice": {
    "title": "Choose an option",
    "hint": "Select the option you want to use as your answer.",
    "more": [
      "This input accepts only the options shown.",
      "You can also type the option name in the message box."
    ]
  },
  "chat.runs": {
    "title": "Runs to continue",
    "hint": "Select one to open its thread and pick up where it stopped.",
    "more": [
      "Runs waiting for your action or interrupted runs appear first.",
      "If there are none, the most recent runs appear.",
      "Opening a thread does not change the run."
    ]
  },
  "chat.question": {
    "title": "Chat question",
    "hint": "Each question is one workflow input.",
    "more": [
      "You can see its name, what it is for, and an example.",
      "Answer in the box below.",
      "You can return to the previous question or skip this one if it is optional."
    ]
  },
  "chat.confirm": {
    "title": "Review before launch",
    "hint": "Review your answers and the prompt before creating the run.",
    "more": [
      "The preview shows the text each step will receive with your answers.",
      "The run is not created until you select “Launch”.",
      "Select “Edit” to return to the questions or “View as form” to open the form."
    ]
  },
  "chat.routing": {
    "title": "Run settings",
    "hint": "Set the mode and preferences for each step before launch.",
    "more": [
      "For each step, you can choose supervised, automatic, or single mode, enable the ECO profile, and set level, model, and effort.",
      "These preferences are saved with the run and checked against the ECO catalog, privacy rules, level limits, and budget.",
      "The chips show preferences, not a promise of which model will run. The preview only shows the prompt."
    ]
  },
  "chat.session_mode": {
    "title": "Session type",
    "hint": "Choose Orchestrator or Single session before you start.",
    "more": [
      "Orchestrator keeps the current workflow questions, team, and plan.",
      "Single session lets you choose a model from the general bundle or a connected executor.",
      "You can change the mode before launch."
    ]
  },
  "chat.connect_other": {
    "title": "Connect another",
    "hint": "Open “Apps and agents” to see what each app or model needs.",
    "more": [
      "Each app and model shows only the step it needs: install it, sign in, set a key, or enable it.",
      "This link only changes the screen. It does not install, sign in, or enable anything."
    ]
  },
  "chat.single_model": {
    "title": "Task model",
    "hint": "Choose a supported, connected model.",
    "more": [
      "Only models in the general bundle catalog with a connected provider appear.",
      "If a provider has no supported model, the reason is shown.",
      "The server checks your choice again when you review and launch."
    ]
  },
  "chat.single_task": {
    "title": "Single-session task",
    "hint": "Write a task in your own words and review it before launch.",
    "more": [
      "The task becomes the only step in the ask_once workflow.",
      "Review shows the prompt and checks the model without running anything.",
      "Launch creates the run and opens its thread."
    ]
  },
  "chat.model_picker": {
    "title": "Connected models and executors",
    "hint": "Choose a checked option for the next single session.",
    "more": [
      "The list shows when each connection was checked and each executor's quota.",
      "This choice does not launch anything or change the current workflow routing."
    ]
  },
  "chat.model_choice": {
    "title": "Choose a model or executor",
    "hint": "Only enabled and connected options can be selected.",
    "more": [
      "Unavailable options are collapsed and show the reason and the next step.",
      "Antigravity uses your subscription and asks for approval at each launch."
    ]
  },
  "chat.model_disconnected": {
    "title": "Unavailable options",
    "hint": "Show options that cannot be chosen yet.",
    "more": [
      "Each option shows the missing step or the reason it is blocked.",
      "After you complete the step, check it again from Apps and agents."
    ]
  },
  "chat.model_retry": {
    "title": "Check connections again",
    "hint": "Read the connection status from the server again.",
    "more": [
      "This does not start a session or run a model.",
      "If it still fails, check that the local server is available."
    ]
  },
  "chat.launch": {
    "title": "Launch",
    "hint": "Create a run with these answers.",
    "more": [
      "This is the only chat button that starts a run.",
      "If the server is not in Mock mode, you must first confirm that real models may be used.",
      "After the run is created, the chat follows its progress."
    ]
  },
  "chat.edit": {
    "title": "Edit answers",
    "hint": "Return to the questions with your answers filled in.",
    "more": [
      "Each question shows your previous answer in the box.",
      "Press Enter to keep it or type a new answer."
    ]
  },
  "chat.retry": {
    "title": "Reload workflows",
    "hint": "Request the workflow list again after an error.",
    "more": [
      "This button appears only when the workflow list could not be read.",
      "If the error continues, open the form with “View as form”."
    ]
  },

  "shell.search": {
    "title": "Search",
    "hint": "Open search (Ctrl K) to find a section, session, run, or help topic.",
    "more": [
      "Search checks each item's title, project, app, and status.",
      "It ignores capitalization and accents, and looks for every word you enter.",
      "It only opens screens and help. It does not search inside conversations."
    ]
  },
  "shell.activities": {
    "title": "Activities",
    "hint": "Show open project activities in the list instead of sessions and runs.",
    "more": [
      "Each activity shows its status and who is working on it. Selecting one opens its project board.",
      "Activities do not count toward project progress.",
      "Select the button again to return to sessions and runs."
    ]
  },

  "chat.executor_launch": {
    "title": "Launch with an executor",
    "hint": "Review the executor, model, effort, and quota before approval.",
    "more": [
      "The task and selection do not start anything. First, a confirmation shows the settings and remaining quota.",
      "Only “Approve and launch” sends your confirmation to the server.",
      "Antigravity shows its notice and follows its switch, disabled credit use, and quota thresholds.",
      "Tracking checks the task status and links to its session in Sessions."
    ]
  },

  "chat.limit_notice": {
    "title": "Usage limit reached",
    "hint": "Shown when a model or executor reaches its quota or rate limit.",
    "more": [
      "It shows “resets at HH:MM” in your local time when available, or “no reset time known”.",
      "A new message never switches models or executors on its own.",
      "Available suggestions appear as buttons for you to choose."
    ]
  },
  "chat.limit_suggestion": {
    "title": "Alternative suggestion",
    "hint": "Select another available option without sending anything.",
    "more": [
      "Your text in the message box is kept.",
      "Only the selected model or executor changes. You still choose when to send or launch."
    ]
  },
  "chat.connection_checks": {
    "title": "Gemini and Copilot status",
    "hint": "Gemini marked “Not checked” is tested when you send; Copilot uses its cache for the month.",
    "more": [
      "“Not checked” means Gemini has a key and can be used, but has not replied in this session.",
      "Copilot can be used only while its local cache for the month is current. Without a valid cache, it remains unchecked.",
      "You can review each connection in “Apps and agents”."
    ]
  },
  "shell.more": {
    "title": "More sections",
    "hint": "Show sections that are not pinned to the bar.",
    "more": ["The other screens are available here.", "Opening a section only changes the view.", "You can pin the sections you use often."]
  },
  "shell.more_notice": {
    "title": "Got it",
    "hint": "Close this first-visit notice.",
    "more": ["The sections are still available under “More”.", "This notice will not appear again in this browser.", "If storage is blocked, the notice may return on the next load."]
  },

  "chat.box_menu": {
    "title": "More options",
    "hint": "Open other ways to get started.",
    "more": ["You can use an executor or open the full form.", "Choosing an option does not launch anything."]
  },
  "chat.project_select": {
    "title": "Session project",
    "hint": "Choose a recent project or confirm one during the flow.",
    "more": ["The guided flow still asks for a project.", "You can change the name before launch.", "Launching with an executor does not use projects."]
  },
  "palette.search": {
    "title": "Search everywhere",
    "hint": "Type to find a section, session, run, or help text.",
    "more": [
      "Use the arrow keys to change results, Enter to open one, and Esc to close the dialog.",
      "Search ignores case and accents, and requires all entered words to appear.",
      "It only opens screens and explains controls; it never approves or starts anything."
    ]
  },
  "palette.close": {
    "title": "Close search",
    "hint": "Close the dialog and return to where you were.",
    "more": ["This does the same as Esc.", "Nothing else changes."]
  },
  "customize.pin": {
    "title": "Pin to the bar",
    "hint": "Add this section to the pinned items in the sidebar.",
    "more": ["Select it again to unpin it and move it under “More”.", "Unpinning does not remove the section. It remains under “More” and in search."]
  },
  "customize.card": {
    "title": "When to show the card",
    "hint": "Automatic: when it has content. Always: even when empty. Never: hidden.",
    "more": ["Urgent items appear in every mode.", "This controls a card in the “Context” panel. Changing it does not delete its data."]
  },
  "customize.help": {
    "title": "Always show help buttons",
    "hint": "Keep the “?” help buttons visible for each control.",
    "more": ["This is the same setting as “Help” and the F1 key.", "When hidden, help is still available when you turn it on."]
  },
  "customize.reset": {
    "title": "Reset",
    "hint": "Restore the default bar, card, help, and color settings.",
    "more": ["Unpin sections, restore the default card settings, turn off help mode, and return to the Neutral color.", "Sessions, runs, and data are not changed."]
  },
  "shell.customize": {
    "title": "Customize",
    "hint": "Open the screen to choose what is shown.",
    "more": ["Pin sections to the bar, choose which “Context” cards appear, and keep help visible.", "This only changes what you see."]
  },
  "customize.accent": {
    "title": "Color palette",
    "hint": "Choose the color for primary buttons, links, and selections.",
    "more": ["Neutral is the usual color. The palette does not change backgrounds, text, or notices.", "This browser remembers your choice."]
  },
  "customize.language": {
    "title": "Language",
    "hint": "Choose the language of this interface in this browser.",
    "more": ["Automatic uses the browser language: Spanish when it begins with es, English otherwise.", "The change applies immediately and is saved in this browser."]
  },

  "customize.advanced_mode": {
    "title": "Advanced mode",
    "hint": "Show all sections. When off, the bar shows only the essentials.",
    "more": ["This setting applies to this installation in every browser and is saved on the server.", "Turning it off hides advanced sections but does not remove them. Pinned sections stay pinned if you turn it on again.", "The interface updates when you change this setting; you do not need to reload the page."]
  },
  "shell.advanced_only": {
    "title": "Advanced-mode view",
    "hint": "This screen is available only in advanced mode.",
    "more": ["Select “Turn on advanced mode” to see it. You can return to simple mode in “Customize”.", "Nothing is lost, and the address stays the same."]
  },

  "chat.plan_draft": {
    "title": "Plan draft",
    "hint": "Your request needs several steps. Review what would be saved; nothing has been saved yet.",
    "more": ["Each task shows its step and the text it would receive. If a bundle needs a team that has not been approved, the draft names the team to propose.", "Nothing is created and the server is not called until you select “Save plan”.", "In advanced mode, each task also shows its workflow and bundle."]
  },
  "chat.office_plan": {
    "title": "Office default plan",
    "hint": "The notice explains why this plan was prepared.",
    "more": [
      "The draft only shows what would be saved. It does not approve or run anything.",
      "“Save plan” stores it for you to review later."
    ]
  },
  "chat.plan_save": {
    "title": "Save plan",
    "hint": "“Save plan” stores it for review. “Cancel” discards it.",
    "more": ["Saving does not approve anything. It opens the plan thread, where you can select “Approve” or “Reject”.", "Missing teams are proposed, not approved, when you save. The thread warns you if you try to approve the plan before the team.", "“Cancel” only closes the draft. It does not save it or remove anything else."]
  },

  "shell.instance_label": {
    "title": "Instance label",
    "hint": "The name set for this instance. It also appears in the tab title.",
    "more": ["Change it with the INSTANCE_LABEL setting, then restart the server to apply it.", "It helps tell two open instances apart. It is only a label and does not change where data is saved."]
  }
};

// One-line hint of each execution mode: the hint of "create.mode" changes with the mode that is selected.
// Keep the keys equal to the modes of the form (auto, local, cloud, human_guided). Also strict JSON.
export const MODE_HINTS = {
  "auto": "Automatic: each step uses the model assigned by the workflow.",
  "local": "Local model: everything runs on your device, with nothing sent to the cloud.",
  "cloud": "Cloud model: uses an outside provider and may cost money.",
  "human_guided": "Human guided: copy the prompt and paste the answer without calling a model."
};

const CREATE_MODE_HELP_KEY = "help.create.mode";

const KIND_SENTENCE = {
  long_text: "Accepts long text with multiple lines.",
  choice: "Choose one option from the list.",
  text: "Accepts a single line of text.",
};

// The entry of "create.mode" with the hint of the selected mode.
export function modeHelpEntry(mode) {
  const translated = t(CREATE_MODE_HELP_KEY);
  const base = typeof translated === "object" ? translated : HELP["create.mode"];
  return { ...base, hint: MODE_HINTS[mode] ? t("create_run.mode_hint_" + mode) : base.hint };
}

// The entry of one declared or derived workflow input (the server sends its name, kind, example and use).
// The visible hint is the declared description when there is one; otherwise it says if the field is mandatory.
export function fieldHelpEntry(item) {
  const required = Boolean(item.required);
  const more = [];
  more.push(item.source === "declared" ? t("create_run.field_kind_" + (KIND_SENTENCE[item.type] ? item.type : "text")) : t("create_run.field_kind_derived"));
  if (required) more.push(t("create_run.field_required"));
  else if (item.skipped_only) more.push(t("create_run.field_optional_skipped"));
  else more.push(t("create_run.field_optional"));
  if (item.example && item.type !== "choice") {
    const example = String(item.example).replace(/\s+/g, " ").trim().slice(0, 100).replace(/[.\s]+$/, "");
    if (example) more.push(t("create_run.field_example", { example }));
  }
  return {
    title: item.label || item.name,
    hint: required ? t("create_run.field_required_hint") : t("create_run.field_optional_hint"),
    more,
  };
}
