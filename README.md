# tune-sublime

Sublime Text package to chat with LLMs directly in a buffer using plain-text “.chat” files. Powered by tune-sdk.

Project Tune: https://github.com/iovdin/tune

## Demo

<video src="https://tuneai.s3.eu-central-003.backblazeb2.com/video/sublime.mp4"></video>

## Quick Start

Prerequisites
- Sublime Text 4 (Build 4100+) recommended
- Node.js 22+ (for tune-sdk)

Steps
1) Install tune-sdk
- npm install -g tune-sdk
- tune-sdk init
- Edit ~/.tune/.env and add OPENAI_KEY (and other provider keys, if needed)

2) Install tune-sublime
- Close Sublime Text
- Clone this repo into your Packages folder as "tune"
  - macOS: ~/Library/Application Support/Sublime Text/Packages/tune
  - Linux: ~/.config/sublime-text/Packages/tune
  - Windows: %AppData%/Sublime Text/Packages/tune
- Reopen Sublime Text

3) Start chatting
- Create new plain text buffer
- Type your message after user:
- Run a chat step: Shift+Enter
- Run until assistant answer:
  - macOS: Cmd+Enter
  - Windows/Linux: Ctrl+Enter
- Cancel generation: Esc or Cmd/Ctrl+C
- Save conversation: press Cmd/Ctrl+S to open Save As with an AI-suggested filename, or use "Tune: Save Name" to just set the tab name

## Why tune-sublime?

- Native editor workflow: stay in Sublime, use familiar keys
- File-based chat: organize conversations as .chat files in your project
- Variable expansion: reference files, environment, buffers, images via @variables
- Tool integration: leverage tune-sdk tools across languages
- Flexible LLM config: choose models/providers in tune-sdk config
- Structured plain text: simple, readable chat format
- Smart completions: snippets for roles + @variable completion
- Suggested filenames: auto-generate meaningful names for chats

## Features

### Chat file format

Use .chat files with clear roles (short or long names):
```
system: system prompt
user: user message
assistant: assistant reply
comment: comment
tool_call: tool call
tool_result: result of a tool call
err: error that occurred
```

### Variable expansion

Include external content in expandable roles (system, user, tool_result):
```
system:
@system           # expand from a file/var
user:
describe @image   # include images or other refs
```

Built-in editor variables provided by this package:
- @editor/filename — current file path
- @editor/buffer — current buffer content
- @editor/buffers — list of open buffers (id and path)
- @editor/selection — current selection text

These are resolved via tune-sdk at runtime.

### Commands (Command Palette)
- Tune: Chat (Step) — run until tool-calling step
- Tune: Chat (Until assistant) — run until assistant message
- Tune: Kill — cancel ongoing generation
- Tune: Save Name — set an AI-suggested name for the current buffer

### Default key bindings

macOS
- Shift+Enter — Tune Chat (step)
- Cmd+Enter — Tune Chat (until assistant)
- Esc or Cmd+C — Tune Kill
- Cmd+S — Tune Auto Save (opens Save As with suggestion in chat/prompt buffers)

Windows/Linux
- Shift+Enter — Tune Chat (step)
- Ctrl+Enter — Tune Chat (until assistant)
- Esc or Ctrl+C — Tune Kill
- Ctrl+S — Tune Auto Save (opens Save As with suggestion in chat/prompt buffers)

### Completion support

- Role snippets: type u, s, or c at column 1, then autocomplete to insert headers
- @variable completion: type @ and get suggestions from tune-sdk

Tip: To trigger @ completion immediately as you type, add this to Chat.sublime-settings (uncomment):
```
"auto_complete_triggers": [
  { "characters": "@", "selector": "text.chat" },
  { "characters": "@", "selector": "text.prompt" }
]
```

### Selection helpers

Commands (no default keymaps):
- tune_select_role {"inner": false|true} — select around/inner role block
- tune_select_chat {"inner": false|true} — select entire chat (between comment headers)
- tune_select_tail {"inner": false|true} — select from current role to end of chat

Example key bindings you can add to your User keymap:
```
{ "keys": ["alt+r"], "command": "tune_select_role" },
{ "keys": ["alt+shift+r"], "command": "tune_select_role", "args": {"inner": true} },
{ "keys": ["alt+c"], "command": "tune_select_chat" },
{ "keys": ["alt+shift+c"], "command": "tune_select_chat", "args": {"inner": true} },
{ "keys": ["alt+t"], "command": "tune_select_tail" },
{ "keys": ["alt+shift+t"], "command": "tune_select_tail", "args": {"inner": true} }
```

### Syntaxes and color scheme

- Chat.sublime-syntax — main chat format (scope: text.chat)
- Prompt.sublime-syntax — loose prompt format (scope: text.prompt)
- Color scheme: color-schemes/Chat.sublime-color-scheme

To force the chat color scheme for Chat files, enable in Chat.sublime-settings:
```
"color_scheme": "Packages/tune/color-schemes/Chat.sublime-color-scheme"
```

### Auto syntax assignment

New plain-text buffers starting with user: or system: are automatically assigned the Chat syntax.

## Configuration

Settings file: Preferences > Package Settings > tune > Chat Settings

Available options
- tune-node-bin: path to a bin directory that contains both node and tune-sdk (recommended)
  - Examples:
    - "~/.nvm/versions/node/v22.20.0/bin"
    - "/opt/node-v22/bin"
- tune-sdk-path: explicit path to the tune-sdk executable (optional)
  - Examples:
    - "tune-sdk" (use PATH)
    - "/usr/local/bin/tune-sdk"
    - "~/.nvm/versions/node/v22.20.0/bin/tune-sdk"
- auto_complete_triggers: enable instant @ completion (see above)
- color_scheme: use the bundled chat scheme

Notes
- If tune-node-bin is set, it will be prepended to PATH for the spawned process so node is available to the tune-sdk shim.
- If tune-sdk-path is not set, the package will look for tune-sdk inside tune-node-bin, then fall back to PATH.

Project context
- The package starts tune-sdk in the first open project folder (if any) to resolve relative paths and tools.

## Installation (manual)

- Clone to Packages as "tune" (see Quick Start)
- Or, drop a .sublime-package built from this repo into Installed Packages

Package Control: will be supported once published to the default channel.

## Troubleshooting

- "tune-sdk not found" or no responses
  - Ensure tune-sdk is installed globally and accessible in PATH
  - Set "tune-sdk-path" in Chat.sublime-settings to an absolute path
  - Run tune-sdk init and configure ~/.tune/.env with provider keys
- No @ completion suggestions
  - Make sure tune-sdk is running (the package starts it on demand)
  - Try reopening Sublime or your project folder to restart the shared RPC
- Nothing happens on keypress
  - Verify the buffer syntax is Chat (or use Command Palette: Set Syntax: Chat)
  - Check the console for errors: Tools > Developer > Toggle Console

## Related

- tune.nvim (Neovim): https://github.com/iovdin/tune.nvim
- tune-sdk (CLI + runtime): https://github.com/iovdin/tune
