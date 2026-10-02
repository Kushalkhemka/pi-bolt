// Benign AOT semantic regression. Exact Pi 1.0 projection helpers; ordinary JSON inputs.
// Source SHA256: 046b6a1109ac3f0ed893bb85bf0648709362fa926a5da75761216cf2fcf9d926
import * as jsc from "bun:jsc";
function buildEntryIndex(entries, byId) {
    if (byId)
        return byId;
    const index = new Map();
    for (const entry of entries) {
        index.set(entry.id, entry);
    }
    return index;
}
function buildSessionPath(entries, leafId, byId) {
    const index = buildEntryIndex(entries, byId);
    let leaf;
    if (leafId === null) {
        return [];
    }
    if (leafId) {
        leaf = index.get(leafId);
    }
    leaf ??= entries[entries.length - 1];
    if (!leaf) {
        return [];
    }
    const path = [];
    let current = leaf;
    while (current) {
        path.push(current);
        current = current.parentId ? index.get(current.parentId) : undefined;
    }
    path.reverse();
    return path;
}
function getSessionContextSettings(path) {
    let thinkingLevel = "off";
    let model = null;
    for (const entry of path) {
        if (entry.type === "thinking_level_change") {
            thinkingLevel = entry.thinkingLevel;
        }
        else if (entry.type === "model_change") {
            model = { provider: entry.provider, modelId: entry.modelId };
        }
        else if (entry.type === "message" && entry.message.role === "assistant") {
            model = { provider: entry.message.provider, modelId: entry.message.model };
        }
    }
    return { thinkingLevel, model };
}
/**
 * Project one selected session entry into LLM/runtime messages.
 * Plain custom entries are display/state entries and do not participate in context.
 */
function sessionEntryToContextMessages(entry) {
    if (entry.type === "message") {
        const message = entry.message;
        // Session files are parsed without validation; old versions, forks, or
        // hand-edited files can contain messages with null/missing content.
        if (message.role === "system" && message.content == null)
            return [{ ...message, content: "" }];
        if ((message.role === "user" || message.role === "assistant" || message.role === "toolResult") &&
            message.content == null) {
            return [{ ...message, content: [] }];
        }
        return [message];
    }
    if (entry.type === "custom_message") {
        return [
            createCustomMessage(entry.customType, entry.content ?? [], entry.display, entry.details, entry.timestamp),
        ];
    }
    if (entry.type === "branch_summary" && entry.summary) {
        return [createBranchSummaryMessage(entry.summary, entry.fromId, entry.timestamp)];
    }
    if (entry.type === "compaction") {
        const summary = createCompactionSummaryMessage(entry.summary, entry.tokensBefore, entry.timestamp);
        return entry.systemMessage ? [entry.systemMessage, summary] : [summary];
    }
    return [];
}
/**
 * Build the active, compaction-aware session entry list.
 *
 * This follows the current leaf path. If the path contains compaction entries,
 * the latest compaction is represented by the compaction entry itself, followed
 * by the kept entries starting at firstKeptEntryId and all entries after the
 * compaction entry. Older summarized entries are omitted.
 */
function buildContextEntries(entries, leafId, byId) {
    const path = buildSessionPath(entries, leafId, byId);
    let compaction = null;
    for (const entry of path) {
        if (entry.type === "compaction") {
            compaction = entry;
        }
    }
    if (!compaction) {
        return path;
    }
    const compactionIdx = path.findIndex((entry) => entry.id === compaction.id);
    if (compactionIdx < 0) {
        return path;
    }
    const contextEntries = [compaction];
    let foundFirstKept = false;
    for (let i = 0; i < compactionIdx; i++) {
        const entry = path[i];
        if (entry.id === compaction.firstKeptEntryId) {
            foundFirstKept = true;
        }
        if (foundFirstKept && !(entry.type === "message" && entry.message.role === "system")) {
            contextEntries.push(entry);
        }
    }
    contextEntries.push(...path.slice(compactionIdx + 1));
    return contextEntries;
}
/**
 * Build the session context from entries using tree traversal.
 * If leafId is provided, walks from that entry to root.
 * Handles compaction and branch summaries along the path.
 */
function projectContextEntry(entry, edit) {
    const messages = sessionEntryToContextMessages(entry);
    if (!edit)
        return messages;
    const replacement = edit.replacement;
    if (replacement === null)
        return [];
    return messages.map((message) => {
        if (message.role !== "user" &&
            message.role !== "assistant" &&
            message.role !== "toolResult" &&
            message.role !== "custom") {
            return message;
        }
        const content = (message.role === "assistant" || message.role === "toolResult") && typeof replacement.content === "string"
            ? [{ type: "text", text: replacement.content }]
            : replacement.content;
        return { ...message, content };
    });
}
/** Build provenance-preserving, compaction-aware model context. */
function buildSessionProjection(entries, leafId, byId) {
    const path = buildSessionPath(entries, leafId, byId);
    const { thinkingLevel, model } = getSessionContextSettings(path);
    const contextEntries = buildContextEntries(entries, leafId, byId);
    const edits = new Map();
    for (const entry of contextEntries) {
        if (entry.type === "context_edit")
            edits.set(entry.targetId, entry);
    }
    const projectedEntries = contextEntries.map((sourceEntry, index) => ({
        sourceEntry,
        // buildContextEntries() may retain an older compaction entry because its
        // raw ID lies inside the newest retained range. Only the newest compaction
        // at index zero contributes a checkpoint and summary.
        messages: sourceEntry.type === "compaction" && index > 0
            ? []
            : projectContextEntry(sourceEntry, edits.get(sourceEntry.id)),
    }));
    return {
        entries: projectedEntries,
        messages: projectedEntries.flatMap((entry) => entry.messages),
        thinkingLevel,
        model,
    };
}

function stringChecks(entry) { return [entry.type === "message", entry.type === "context_edit", entry.type === "abcdefghijklmnop", entry.type === "thinking_level_change", entry.replacement === null]; }
function collectEdits(entries) { const edits=new Map(); for(const entry of entries) if(entry.type === "context_edit") edits.set(entry.targetId,entry); return edits; }
function summarizeProjection(entries) {
 const result=buildSessionProjection(entries);
 return {count:result.messages.length, targets:result.entries.filter(e=>e.sourceEntry.type === "message").map(e=>[e.sourceEntry.id,e.messages.length]), types:entries.map(stringChecks), editKeys:Array.from(collectEdits(entries).keys())};
}
const text="[{\"type\":\"model_change\",\"id\":\"8251a988\",\"parentId\":null,\"timestamp\":\"2026-10-02T07:58:14.678Z\",\"provider\":\"workflow\",\"modelId\":\"mock\"},{\"type\":\"thinking_level_change\",\"id\":\"5033ad7c\",\"parentId\":\"8251a988\",\"timestamp\":\"2026-10-02T07:58:14.678Z\",\"thinkingLevel\":\"off\"},{\"type\":\"custom\",\"customType\":\"workflow-dialog\",\"data\":{\"selected\":\"beta\",\"confirmed\":true},\"id\":\"7e9c79c6\",\"parentId\":\"5033ad7c\",\"timestamp\":\"2026-10-02T07:58:14.684Z\"},{\"type\":\"message\",\"id\":\"8590da88\",\"parentId\":\"7e9c79c6\",\"timestamp\":\"2026-10-02T07:58:14.685Z\",\"message\":{\"role\":\"system\",\"content\":[{\"type\":\"text\",\"text\":\"ordinary\"}],\"sections\":{\"preamble\":\"You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.\",\"tools\":\"<tools>\\n- read: Read file contents\\n- bash: Execute bash commands (ls, grep, find, etc.)\\n- edit: Make precise file edits with exact text replacement, including multiple disjoint edits in one call\\n- write: Create or overwrite files\\n\\nIn addition to the tools above, you may have access to other custom tools depending on the project.\\n</tools>\",\"rules\":\"<rules>\\n- Use bash for file operations like ls, rg, find\\n- Use read to examine files instead of cat or sed.\\n- You can inspect PI_* environment variables for current model and session details.\\n- Use edit for precise changes (edits[].oldText must match exactly)\\n- When changing multiple separate locations in one file, use one edit call with multiple entries in edits[] instead of multiple edit calls\\n- Each edits[].oldText is matched against the original file, not after earlier edits are applied. Do not emit overlapping or nested edits. Merge nearby changes into one edit.\\n- Keep edits[].oldText as small as possible while still being unique in the file. Do not pad with large unchanged regions.\\n- Use write only for new files or complete rewrites.\\n- Be concise in your responses\\n- Show file paths clearly when working with files\\n</rules>\",\"docs\":\"<docs>\\nPi documentation (read only when the user asks about pi itself, its SDK, extensions, themes, skills, or TUI):\\n- Main documentation: /Users/kushalkhemka/Desktop/Pi-Bolt/native-aot/artifacts/darwin-arm64-m5-pi-v1-priority/README.md\\n- Additional docs: /Users/kushalkhemka/Desktop/Pi-Bolt/native-aot/artifacts/darwin-arm64-m5-pi-v1-priority/docs\\n- Examples: /Users/kushalkhemka/Desktop/Pi-Bolt/native-aot/artifacts/darwin-arm64-m5-pi-v1-priority/examples (extensions, custom tools, SDK)\\n- When reading pi docs or examples, resolve docs/... under Additional docs and examples/... under Examples, not the current working directory\\n- When asked about: extensions (docs/extensions.md, examples/extensions/), themes (docs/themes.md), skills (docs/skills.md), prompt templates (docs/prompt-templates.md), TUI components (docs/tui.md), keybindings (docs/keybindings.md), SDK integrations (docs/sdk.md), custom providers (docs/custom-provider.md), adding models (docs/models.md), pi packages (docs/packages.md), environment variables (docs/environment-variables.md), MCP servers (docs/mcp.md), codemode scripts and non-LLM models such as classifiers and image models (docs/codemode.md)\\n- When working on pi topics, read the docs and examples, and follow .md cross-references before implementing\\n- Always read pi .md files completely and follow links to related docs (e.g., tui.md for TUI API details)\\n</docs>\",\"cwd\":\"<cwd>\\n/private/var/folders/px/yl7g3y_17zlcpc5dpd1lw34w0000gn/T/pi-cli-workflows-8p7b664u\\n</cwd>\"},\"timestamp\":1790927894684,\"toolsAdded\":[{\"name\":\"read\",\"description\":\"Read the contents of a file. Supports text files and images (jpg, png, gif, webp, bmp). Images are sent as attachments. For text files, output is truncated to 2000 lines or 50KB (whichever is hit first). Use offset/limit for large files. When you need the full file, continue with offset until complete.\",\"parameters\":{\"type\":\"object\",\"required\":[\"path\"],\"properties\":{\"path\":{\"type\":\"string\",\"description\":\"Path to the file to read (relative or absolute)\"},\"offset\":{\"type\":\"number\",\"description\":\"Line number to start reading from (1-indexed)\"},\"limit\":{\"type\":\"number\",\"description\":\"Maximum number of lines to read\"}}},\"constrainedSampling\":{\"type\":\"json_schema\",\"strict\":\"prefer\"}},{\"name\":\"bash\",\"description\":\"Execute a bash command in the current working directory. Returns stdout and stderr. Output is truncated to last 2000 lines or 50KB (whichever is hit first). If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds.\",\"parameters\":{\"type\":\"object\",\"required\":[\"command\"],\"properties\":{\"command\":{\"type\":\"string\",\"description\":\"Shell command to execute\"},\"timeout\":{\"type\":\"number\",\"description\":\"Timeout in seconds (optional, no default timeout)\"}}},\"constrainedSampling\":{\"type\":\"json_schema\",\"strict\":\"prefer\"}},{\"name\":\"edit\",\"description\":\"Edit a single file using exact text replacement. Every edits[].oldText must match a unique, non-overlapping region of the original file. If two changes affect the same block or nearby lines, merge them into one edit instead of emitting overlapping edits. Do not include large unchanged regions just to connect distant changes.\",\"parameters\":{\"type\":\"object\",\"required\":[\"path\",\"edits\"],\"properties\":{\"path\":{\"type\":\"string\",\"description\":\"Path to the file to edit (relative or absolute)\"},\"edits\":{\"type\":\"array\",\"items\":{\"type\":\"object\",\"required\":[\"oldText\",\"newText\"],\"properties\":{\"oldText\":{\"type\":\"string\",\"description\":\"Exact text for one targeted replacement. It must be unique in the original file and must not overlap with any other edits[].oldText in the same call.\"},\"newText\":{\"type\":\"string\",\"description\":\"Replacement text for this targeted edit.\"}}},\"description\":\"One or more targeted replacements. Each edit is matched against the original file, not incrementally. Do not include overlapping or nested edits. If two changes touch the same block or nearby lines, merge them into one edit instead.\"}}},\"constrainedSampling\":{\"type\":\"json_schema\",\"strict\":\"prefer\"}},{\"name\":\"write\",\"description\":\"Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Automatically creates parent directories.\",\"parameters\":{\"type\":\"object\",\"required\":[\"path\",\"content\"],\"properties\":{\"path\":{\"type\":\"string\",\"description\":\"Path to the file to write (relative or absolute)\"},\"content\":{\"type\":\"string\",\"description\":\"Content to write to the file\"}}},\"constrainedSampling\":{\"type\":\"json_schema\",\"strict\":\"prefer\"}}]}},{\"type\":\"message\",\"id\":\"cb565dfd\",\"parentId\":\"8590da88\",\"timestamp\":\"2026-10-02T07:58:14.685Z\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"ordinary\"}],\"timestamp\":1790927894684}},{\"type\":\"message\",\"id\":\"77ea3250\",\"parentId\":\"cb565dfd\",\"timestamp\":\"2026-10-02T07:58:14.695Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"error\"}],\"api\":\"openai-completions\",\"provider\":\"workflow\",\"model\":\"mock\",\"usage\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"totalTokens\":0,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"total\":0}},\"stopReason\":\"error\",\"timestamp\":1790927894691,\"errorMessage\":\"500: {\\\"message\\\":\\\"EXPECTED_WORKFLOW_PROVIDER_ERROR\\\",\\\"type\\\":\\\"server_error\\\"}\",\"thinkingLevel\":\"off\"}},{\"type\":\"message\",\"id\":\"7be5a90d\",\"parentId\":\"77ea3250\",\"timestamp\":\"2026-10-02T07:58:14.696Z\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"ordinary\"}],\"timestamp\":1790927894696}},{\"type\":\"message\",\"id\":\"011f1460\",\"parentId\":\"7be5a90d\",\"timestamp\":\"2026-10-02T07:58:14.700Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"stop\"}],\"api\":\"openai-completions\",\"provider\":\"workflow\",\"model\":\"mock\",\"usage\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"totalTokens\":0,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"total\":0}},\"stopReason\":\"stop\",\"timestamp\":1790927894696,\"responseId\":\"workflow\",\"rawStopReason\":\"stop\",\"thinkingLevel\":\"off\"}},{\"type\":\"message\",\"id\":\"9ea2b07e\",\"parentId\":\"011f1460\",\"timestamp\":\"2026-10-02T07:58:14.701Z\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"ordinary\"}],\"timestamp\":1790927894701}},{\"type\":\"message\",\"id\":\"9fd9f52e\",\"parentId\":\"9ea2b07e\",\"timestamp\":\"2026-10-02T07:58:14.704Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"error\"}],\"api\":\"openai-completions\",\"provider\":\"workflow\",\"model\":\"mock\",\"usage\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"totalTokens\":0,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"total\":0}},\"stopReason\":\"error\",\"timestamp\":1790927894701,\"errorMessage\":\"429: {\\\"message\\\":\\\"EXPECTED_WORKFLOW_RETRY_ERROR\\\",\\\"type\\\":\\\"rate_limit_error\\\"}\",\"thinkingLevel\":\"off\"}},{\"type\":\"context_edit\",\"id\":\"86c9fd20\",\"parentId\":\"9fd9f52e\",\"timestamp\":\"2026-10-02T07:58:14.704Z\",\"targetId\":\"9fd9f52e\",\"replacement\":null},{\"type\":\"message\",\"id\":\"0b516b0e\",\"parentId\":\"86c9fd20\",\"timestamp\":\"2026-10-02T07:58:14.706Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"stop\"}],\"api\":\"openai-completions\",\"provider\":\"workflow\",\"model\":\"mock\",\"usage\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"totalTokens\":0,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"total\":0}},\"stopReason\":\"stop\",\"timestamp\":1790927894706,\"responseId\":\"workflow\",\"rawStopReason\":\"stop\",\"thinkingLevel\":\"off\"}},{\"type\":\"message\",\"id\":\"91df0c13\",\"parentId\":\"0b516b0e\",\"timestamp\":\"2026-10-02T07:58:14.707Z\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"ordinary\"}],\"timestamp\":1790927894707}},{\"type\":\"message\",\"id\":\"43c119dc\",\"parentId\":\"91df0c13\",\"timestamp\":\"2026-10-02T07:58:14.707Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"toolUse\"}],\"api\":\"openai-completions\",\"provider\":\"workflow\",\"model\":\"mock\",\"usage\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"totalTokens\":0,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"total\":0}},\"stopReason\":\"toolUse\",\"timestamp\":1790927894707,\"responseId\":\"workflow\",\"rawStopReason\":\"tool_calls\",\"thinkingLevel\":\"off\"}},{\"type\":\"message\",\"id\":\"03abbbc5\",\"parentId\":\"43c119dc\",\"timestamp\":\"2026-10-02T07:58:14.708Z\",\"message\":{\"role\":\"toolResult\",\"toolCallId\":\"missing-file\",\"toolName\":\"read\",\"content\":[{\"type\":\"text\",\"text\":\"ordinary\"}],\"details\":{},\"isError\":true,\"timestamp\":1790927894708}},{\"type\":\"message\",\"id\":\"0bf2dc1d\",\"parentId\":\"03abbbc5\",\"timestamp\":\"2026-10-02T07:58:14.709Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"stop\"}],\"api\":\"openai-completions\",\"provider\":\"workflow\",\"model\":\"mock\",\"usage\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"totalTokens\":0,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"total\":0}},\"stopReason\":\"stop\",\"timestamp\":1790927894708,\"responseId\":\"workflow\",\"rawStopReason\":\"stop\",\"thinkingLevel\":\"off\"}},{\"type\":\"model_change\",\"id\":\"d35e7f08\",\"parentId\":\"0bf2dc1d\",\"timestamp\":\"2026-10-02T07:58:14.709Z\",\"provider\":\"workflow\",\"modelId\":\"mock-two\"},{\"type\":\"message\",\"id\":\"20073c0c\",\"parentId\":\"d35e7f08\",\"timestamp\":\"2026-10-02T07:58:14.709Z\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"ordinary\"}],\"timestamp\":1790927894709}},{\"type\":\"message\",\"id\":\"d5dac9f3\",\"parentId\":\"20073c0c\",\"timestamp\":\"2026-10-02T07:58:14.710Z\",\"message\":{\"role\":\"assistant\",\"content\":[{\"type\":\"text\",\"text\":\"stop\"}],\"api\":\"openai-completions\",\"provider\":\"workflow\",\"model\":\"mock-two\",\"usage\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"totalTokens\":0,\"cost\":{\"input\":0,\"output\":0,\"cacheRead\":0,\"cacheWrite\":0,\"total\":0}},\"stopReason\":\"stop\",\"timestamp\":1790927894709,\"responseId\":\"workflow\",\"rawStopReason\":\"stop\",\"thinkingLevel\":\"off\"}},{\"type\":\"session_info\",\"id\":\"826084de\",\"parentId\":\"d5dac9f3\",\"timestamp\":\"2026-10-02T07:58:14.710Z\",\"name\":\"workflow-session\"}]";
const parsed=JSON.parse(text); const literal=[{"type":"model_change","id":"8251a988","parentId":null,"timestamp":"2026-10-02T07:58:14.678Z","provider":"workflow","modelId":"mock"},{"type":"thinking_level_change","id":"5033ad7c","parentId":"8251a988","timestamp":"2026-10-02T07:58:14.678Z","thinkingLevel":"off"},{"type":"custom","customType":"workflow-dialog","data":{"selected":"beta","confirmed":true},"id":"7e9c79c6","parentId":"5033ad7c","timestamp":"2026-10-02T07:58:14.684Z"},{"type":"message","id":"8590da88","parentId":"7e9c79c6","timestamp":"2026-10-02T07:58:14.685Z","message":{"role":"system","content":[{"type":"text","text":"ordinary"}],"sections":{"preamble":"You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.","tools":"<tools>\n- read: Read file contents\n- bash: Execute bash commands (ls, grep, find, etc.)\n- edit: Make precise file edits with exact text replacement, including multiple disjoint edits in one call\n- write: Create or overwrite files\n\nIn addition to the tools above, you may have access to other custom tools depending on the project.\n</tools>","rules":"<rules>\n- Use bash for file operations like ls, rg, find\n- Use read to examine files instead of cat or sed.\n- You can inspect PI_* environment variables for current model and session details.\n- Use edit for precise changes (edits[].oldText must match exactly)\n- When changing multiple separate locations in one file, use one edit call with multiple entries in edits[] instead of multiple edit calls\n- Each edits[].oldText is matched against the original file, not after earlier edits are applied. Do not emit overlapping or nested edits. Merge nearby changes into one edit.\n- Keep edits[].oldText as small as possible while still being unique in the file. Do not pad with large unchanged regions.\n- Use write only for new files or complete rewrites.\n- Be concise in your responses\n- Show file paths clearly when working with files\n</rules>","docs":"<docs>\nPi documentation (read only when the user asks about pi itself, its SDK, extensions, themes, skills, or TUI):\n- Main documentation: /Users/kushalkhemka/Desktop/Pi-Bolt/native-aot/artifacts/darwin-arm64-m5-pi-v1-priority/README.md\n- Additional docs: /Users/kushalkhemka/Desktop/Pi-Bolt/native-aot/artifacts/darwin-arm64-m5-pi-v1-priority/docs\n- Examples: /Users/kushalkhemka/Desktop/Pi-Bolt/native-aot/artifacts/darwin-arm64-m5-pi-v1-priority/examples (extensions, custom tools, SDK)\n- When reading pi docs or examples, resolve docs/... under Additional docs and examples/... under Examples, not the current working directory\n- When asked about: extensions (docs/extensions.md, examples/extensions/), themes (docs/themes.md), skills (docs/skills.md), prompt templates (docs/prompt-templates.md), TUI components (docs/tui.md), keybindings (docs/keybindings.md), SDK integrations (docs/sdk.md), custom providers (docs/custom-provider.md), adding models (docs/models.md), pi packages (docs/packages.md), environment variables (docs/environment-variables.md), MCP servers (docs/mcp.md), codemode scripts and non-LLM models such as classifiers and image models (docs/codemode.md)\n- When working on pi topics, read the docs and examples, and follow .md cross-references before implementing\n- Always read pi .md files completely and follow links to related docs (e.g., tui.md for TUI API details)\n</docs>","cwd":"<cwd>\n/private/var/folders/px/yl7g3y_17zlcpc5dpd1lw34w0000gn/T/pi-cli-workflows-8p7b664u\n</cwd>"},"timestamp":1790927894684,"toolsAdded":[{"name":"read","description":"Read the contents of a file. Supports text files and images (jpg, png, gif, webp, bmp). Images are sent as attachments. For text files, output is truncated to 2000 lines or 50KB (whichever is hit first). Use offset/limit for large files. When you need the full file, continue with offset until complete.","parameters":{"type":"object","required":["path"],"properties":{"path":{"type":"string","description":"Path to the file to read (relative or absolute)"},"offset":{"type":"number","description":"Line number to start reading from (1-indexed)"},"limit":{"type":"number","description":"Maximum number of lines to read"}}},"constrainedSampling":{"type":"json_schema","strict":"prefer"}},{"name":"bash","description":"Execute a bash command in the current working directory. Returns stdout and stderr. Output is truncated to last 2000 lines or 50KB (whichever is hit first). If truncated, full output is saved to a temp file. Optionally provide a timeout in seconds.","parameters":{"type":"object","required":["command"],"properties":{"command":{"type":"string","description":"Shell command to execute"},"timeout":{"type":"number","description":"Timeout in seconds (optional, no default timeout)"}}},"constrainedSampling":{"type":"json_schema","strict":"prefer"}},{"name":"edit","description":"Edit a single file using exact text replacement. Every edits[].oldText must match a unique, non-overlapping region of the original file. If two changes affect the same block or nearby lines, merge them into one edit instead of emitting overlapping edits. Do not include large unchanged regions just to connect distant changes.","parameters":{"type":"object","required":["path","edits"],"properties":{"path":{"type":"string","description":"Path to the file to edit (relative or absolute)"},"edits":{"type":"array","items":{"type":"object","required":["oldText","newText"],"properties":{"oldText":{"type":"string","description":"Exact text for one targeted replacement. It must be unique in the original file and must not overlap with any other edits[].oldText in the same call."},"newText":{"type":"string","description":"Replacement text for this targeted edit."}}},"description":"One or more targeted replacements. Each edit is matched against the original file, not incrementally. Do not include overlapping or nested edits. If two changes touch the same block or nearby lines, merge them into one edit instead."}}},"constrainedSampling":{"type":"json_schema","strict":"prefer"}},{"name":"write","description":"Write content to a file. Creates the file if it doesn't exist, overwrites if it does. Automatically creates parent directories.","parameters":{"type":"object","required":["path","content"],"properties":{"path":{"type":"string","description":"Path to the file to write (relative or absolute)"},"content":{"type":"string","description":"Content to write to the file"}}},"constrainedSampling":{"type":"json_schema","strict":"prefer"}}]}},{"type":"message","id":"cb565dfd","parentId":"8590da88","timestamp":"2026-10-02T07:58:14.685Z","message":{"role":"user","content":[{"type":"text","text":"ordinary"}],"timestamp":1790927894684}},{"type":"message","id":"77ea3250","parentId":"cb565dfd","timestamp":"2026-10-02T07:58:14.695Z","message":{"role":"assistant","content":[{"type":"text","text":"error"}],"api":"openai-completions","provider":"workflow","model":"mock","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"error","timestamp":1790927894691,"errorMessage":"500: {\"message\":\"EXPECTED_WORKFLOW_PROVIDER_ERROR\",\"type\":\"server_error\"}","thinkingLevel":"off"}},{"type":"message","id":"7be5a90d","parentId":"77ea3250","timestamp":"2026-10-02T07:58:14.696Z","message":{"role":"user","content":[{"type":"text","text":"ordinary"}],"timestamp":1790927894696}},{"type":"message","id":"011f1460","parentId":"7be5a90d","timestamp":"2026-10-02T07:58:14.700Z","message":{"role":"assistant","content":[{"type":"text","text":"stop"}],"api":"openai-completions","provider":"workflow","model":"mock","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop","timestamp":1790927894696,"responseId":"workflow","rawStopReason":"stop","thinkingLevel":"off"}},{"type":"message","id":"9ea2b07e","parentId":"011f1460","timestamp":"2026-10-02T07:58:14.701Z","message":{"role":"user","content":[{"type":"text","text":"ordinary"}],"timestamp":1790927894701}},{"type":"message","id":"9fd9f52e","parentId":"9ea2b07e","timestamp":"2026-10-02T07:58:14.704Z","message":{"role":"assistant","content":[{"type":"text","text":"error"}],"api":"openai-completions","provider":"workflow","model":"mock","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"error","timestamp":1790927894701,"errorMessage":"429: {\"message\":\"EXPECTED_WORKFLOW_RETRY_ERROR\",\"type\":\"rate_limit_error\"}","thinkingLevel":"off"}},{"type":"context_edit","id":"86c9fd20","parentId":"9fd9f52e","timestamp":"2026-10-02T07:58:14.704Z","targetId":"9fd9f52e","replacement":null},{"type":"message","id":"0b516b0e","parentId":"86c9fd20","timestamp":"2026-10-02T07:58:14.706Z","message":{"role":"assistant","content":[{"type":"text","text":"stop"}],"api":"openai-completions","provider":"workflow","model":"mock","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop","timestamp":1790927894706,"responseId":"workflow","rawStopReason":"stop","thinkingLevel":"off"}},{"type":"message","id":"91df0c13","parentId":"0b516b0e","timestamp":"2026-10-02T07:58:14.707Z","message":{"role":"user","content":[{"type":"text","text":"ordinary"}],"timestamp":1790927894707}},{"type":"message","id":"43c119dc","parentId":"91df0c13","timestamp":"2026-10-02T07:58:14.707Z","message":{"role":"assistant","content":[{"type":"text","text":"toolUse"}],"api":"openai-completions","provider":"workflow","model":"mock","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"toolUse","timestamp":1790927894707,"responseId":"workflow","rawStopReason":"tool_calls","thinkingLevel":"off"}},{"type":"message","id":"03abbbc5","parentId":"43c119dc","timestamp":"2026-10-02T07:58:14.708Z","message":{"role":"toolResult","toolCallId":"missing-file","toolName":"read","content":[{"type":"text","text":"ordinary"}],"details":{},"isError":true,"timestamp":1790927894708}},{"type":"message","id":"0bf2dc1d","parentId":"03abbbc5","timestamp":"2026-10-02T07:58:14.709Z","message":{"role":"assistant","content":[{"type":"text","text":"stop"}],"api":"openai-completions","provider":"workflow","model":"mock","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop","timestamp":1790927894708,"responseId":"workflow","rawStopReason":"stop","thinkingLevel":"off"}},{"type":"model_change","id":"d35e7f08","parentId":"0bf2dc1d","timestamp":"2026-10-02T07:58:14.709Z","provider":"workflow","modelId":"mock-two"},{"type":"message","id":"20073c0c","parentId":"d35e7f08","timestamp":"2026-10-02T07:58:14.709Z","message":{"role":"user","content":[{"type":"text","text":"ordinary"}],"timestamp":1790927894709}},{"type":"message","id":"d5dac9f3","parentId":"20073c0c","timestamp":"2026-10-02T07:58:14.710Z","message":{"role":"assistant","content":[{"type":"text","text":"stop"}],"api":"openai-completions","provider":"workflow","model":"mock-two","usage":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"totalTokens":0,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0,"total":0}},"stopReason":"stop","timestamp":1790927894709,"responseId":"workflow","rawStopReason":"stop","thinkingLevel":"off"}},{"type":"session_info","id":"826084de","parentId":"d5dac9f3","timestamp":"2026-10-02T07:58:14.710Z","name":"workflow-session"}]; const roundtrip=JSON.parse(JSON.stringify(literal));
const minimal=JSON.parse('[{"type":"message","id":"a","parentId":null,"message":{"role":"assistant","content":[],"stopReason":"error"}},{"type":"context_edit","id":"b","parentId":"a","targetId":"a","replacement":null},{"type":"message","id":"c","parentId":"b","message":{"role":"assistant","content":[],"stopReason":"stop"}}]');
const strings=["message","context_edit","abcdefghijklmnop","thinking_level_change","not_context_"];
const comparisons=strings.flatMap(type=>[JSON.parse(JSON.stringify({type,replacement:null})),{type:("X"+type).slice(1),replacement:null},{type:(type.slice(0,3)+type.slice(3)),replacement:null}]).map(stringChecks);
const first={parsed:summarizeProjection(parsed),literal:summarizeProjection(literal),roundtrip:summarizeProjection(roundtrip),minimal:summarizeProjection(minimal),comparisons};
function classifyType(entry) {
 switch (entry.type) {
  case "context_edit": return 1;
  case "abcdefghijklmnop": return 2;
  case "message": return 3;
  case "": return 4;
  case "é": return 5;
  case "中文": return 6;
  default: return 0;
 }
}
function editCase(replacement, duplicate) {
 const original={type:"message",id:"target_a",parentId:null,message:{role:"assistant",content:[{type:"text",text:"old"}]}};
 const edit={type:"context_edit",id:"edit_a",parentId:"target_a",targetId:("Xtarget_a").slice(1),replacement};
 const final={type:"message",id:"final_a",parentId:"edit_a",message:{role:"assistant",content:[{type:"text",text:"last"}]}};
 const values=[original,edit,final];
 if(duplicate) {
  final.parentId="edit_b";
  values.splice(2,0,{...edit,id:"edit_b",parentId:"edit_a",replacement:null});
 }
 const parsed=JSON.parse(JSON.stringify(values));
 const result=buildSessionProjection(parsed);
 return result.messages.map(message=>message.content);
}
const editCases={null:editCase(null,false),string:editCase({content:"new"},false),array:editCase({content:[{type:"text",text:"array"}]},false),lastWins:editCase({content:"new"},true)};
const unchanged=projectContextEntry(minimal[0],undefined).map(message=>message.content);
const constantStrings=["context_edit","abcdefghijklmnop","","a","ab","abc","é","中文","a longer ordinary literal for mapped payload reconstruction"];
const poolChecks=constantStrings.map(value=>{const parsed=JSON.parse(JSON.stringify(value));return [parsed===value,classifyType({type:parsed})];});
const captures=JSON.parse('[{"targetId":"target_a","replacement":null},{"targetId":"second_a","replacement":{"content":"new"}}]');
const capturedMap=new Map(captures.map(edit=>[edit.targetId,edit]));
const captureChecks=["target_a","second_a","missing_a"].map(id=>{const edit=capturedMap.get(("X"+id).slice(1));return [!!edit,edit?.replacement===null,edit?.replacement?.content??null];});
jsc.gcAndSweep(); const afterGC=summarizeProjection(JSON.parse(text)); console.log(JSON.stringify({first,afterGC,editCases,unchanged,poolChecks,captureChecks}));
