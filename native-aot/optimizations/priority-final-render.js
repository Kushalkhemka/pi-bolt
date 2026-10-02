// Build-time Pi transform, checked against 0.85.1 and 1.0.0 published sources.
// Exact source guards remain authoritative; streaming retains its scheduler.
const FINAL_BRANCH = `            case "agent_end":
                if (this.settingsManager.getShowTerminalProgress()) {
                    this.ui.terminal.setProgress(false);
                }
                this.clearStatusIndicator("working");
                if (this.streamingComponent) {
                    this.chatContainer.removeChild(this.streamingComponent);
                    this.streamingComponent = undefined;
                    this.streamingMessage = undefined;
                }
                this.pendingTools.clear();
                this.ui.requestRender();
                break;
            case "agent_settled":`;

// Keep the existing stopped/coalescing/cancellation semantics. A new upstream
// scheduler must be reviewed, rather than silently assuming its private API.
const SCHEDULER = `    requestImmediateRender() {
        this.cancelRenderTimer();
        this.renderRequested = true;
        if (this.immediateRenderScheduled)
            return;
        this.immediateRenderScheduled = true;
        process.nextTick(() => {
            this.immediateRenderScheduled = false;
            if (this.stopped || !this.renderRequested)
                return;
            // A previously queued scheduleRender() can create a timer before this
            // callback runs. User input must preempt that throttled frame.
            this.cancelRenderTimer();
            this.renderRequested = false;
            this.lastRenderAt = performance.now();
            this.doRender();
        });
    }
    cancelRenderTimer() {
        if (!this.renderTimer)
            return;
        clearTimeout(this.renderTimer);
        this.renderTimer = undefined;
    }
    scheduleRender() {
        if (this.stopped || this.renderTimer || !this.renderRequested) {
            return;
        }
        const elapsed = performance.now() - this.lastRenderAt;
        const delay = Math.max(0, TuiBase.MIN_RENDER_INTERVAL_MS - elapsed);
        this.renderTimer = setTimeout(() => {
            this.renderTimer = undefined;
            if (this.stopped || !this.renderRequested) {
                return;
            }
            this.renderRequested = false;
            this.lastRenderAt = performance.now();
            this.doRender();
            if (this.renderRequested) {
                this.scheduleRender();
            }
        }, delay);
    }`;

function requireOnce(source, expected, description) {
    if (typeof source !== "string" || source.split(expected).length !== 2)
        throw new Error(`Pi ${description} changed; revalidate the priority final-render optimization before building`);
}

export function validatePrioritySchedulerSource(source) {
    requireOnce(source, SCHEDULER, "TUI priority scheduler");
    requireOnce(source, "    static MIN_RENDER_INTERVAL_MS = 16;", "TUI stream throttle");
}

export function transformPriorityFinalRender(source, schedulerSource) {
    validatePrioritySchedulerSource(schedulerSource);
    requireOnce(source, FINAL_BRANCH, "agent_end render branch");
    return source.replace(FINAL_BRANCH,
        FINAL_BRANCH.replace("this.ui.requestRender();", "this.ui.requestImmediateRender();"));
}
