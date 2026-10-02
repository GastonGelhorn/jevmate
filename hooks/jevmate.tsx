// The jevmate mod: the part of the plugin that lives inside Claude Code's own process.
//
// The Python hooks (jev/hooks.py) do the deciding; this module does what a hook command cannot:
// draw the session's numbers above the prompt and in a pane, put a line under a reply that claims
// a check passed when none ran, ask the person about a destructive command where no permission
// prompt can appear (bypassPermissions), lower the effort or change the model for a prompt jev
// rated routine, and say what another mod reaches for as it loads. Every call into jev goes
// through `python3 <plugin>/bin/jev`, so the thresholds and the ledger stay the CLI's.
//
// Needs Claude Code 2.1.287 or later; older versions ignore `modules` and keep the hooks alone.

import type { EngineInterface, Register } from 'claude-code'

type Api = EngineInterface

// Kept in step with jev/hooks.py: the cheap skip before any process is started.
const SAFE =
  /^\s*(ls|cat|head|tail|wc|grep|rg|find|fd|echo|printf|pwd|which|type|file|stat|du|df|env|printenv|date|whoami|id|uname|tree|less|git\s+(status|log|diff|show|branch|remote|rev-parse|describe|blame|stash\s+list|check-ignore)|python3?\s+-m\s+py_compile|jev|shasum|md5|sha256sum|diff|cmp)\b/
const RISKY =
  /[>|;&`$]|\b(rm|mv|dd|mkfs|chmod|chown|kill|pkill|curl|wget|sudo|truncate|drop|delete|push|reset|rebase|checkout|clean|prune|purge|format|shred)\b|--force|--hard|\s-[a-zA-Z]*f/
const CLAIM =
  /\b(tests?|suite|build|lint|checks?|typecheck|ci)\b[^.\n]{0,60}\b(pass(es|ed|ing)?|green|succeed(s|ed)?|clean|ok)\b|\b(all green|verified|confirmed working|works as expected)\b/i
const RUNNER =
  /\b(pytest|phpunit|npm (run )?test|yarn test|pnpm test|go test|cargo test|make test|jest|vitest|mocha|unittest|composer test|gradlew? test|mvn test|dotnet test|rspec|bundle exec|tsc|mypy|ruff|eslint|flake8|pint|phpstan|psalm|cargo (check|clippy)|npm run (build|lint)|make)\b/i
const CREDENTIAL = /(TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE_KEY)/i

const PANE = 'jev'
const COMMANDS = ['jevmate', 'jev-session'] as const

// Theme keys, so the colors follow the person's theme, light or dark, terminal or desktop.
const ACCENT = 'claude'
const GOOD = 'success'
const WARN = 'warning'
const BAD = 'error'
const INFO = 'suggestion'
const LABEL = 18 // the pane's label column

type JevSide = {
  requests: number
  decisions: number
  tokens: number
  cached: number
  trimmed: number
  trim_runs: number
  kept_out: number
  paid: number
  would: number
  saved: number
  asked: number
  once?: number
  reread?: number
  labels?: Record<string, number>
  share?: number
}
type Summary = {
  session: string | null
  cwd?: string
  model: { usd: number; turns: number; ctx: number; ctx_size: number } | null
  jev: JevSide
}
// One styled run of text, and a group of them that the band keeps or drops as a whole.
type Piece = { text: string; color?: string; bold?: true; dim?: true }
type Segment = { key: string; rank: number; pieces: Piece[] }
type Judge = {
  decision: string
  why?: string | null
  p?: number
  outside?: number | null
  requested?: number | null
  ask_at?: number
  deny_at?: number
  reason?: string
}
type Route = { kind: 'effort' | 'model'; model: string; name: string; conf: number }

// Module state. A reload starts it over; nothing here is worth keeping past that.
let cfg = { guardMode: 'ask', bandMode: 'on', evidenceLine: 'on', routeMode: 'off', routeModel: 'haiku', routeConf: 0.8 }
let optionEnv: Record<string, string> = {}
let sessionId = ''
let cwd = ''
let canAsk = false
let launcher: string[] | null = null
let summary: Summary | null = null
let ctxPct: number | null = null
let sessionUsd: number | null = null
let refreshedAt = ''
let collapsed = false
let paneOpen = false
let lastPrompt = ''
let turnCommands: string[] = []
let pendingRoute: Route | null = null
const routeFor = new Map<string, Route>()
const flagged = new Set<string>()
let refreshing = false
let refreshAgain = false

const k = (x: number) => (x >= 1e6 ? `${(x / 1e6).toFixed(1)}M` : x >= 1000 ? `${Math.round(x / 1000)}k` : String(x))
const n = (x: number) => x.toLocaleString('en-US')
const money = (x: number) => (x >= 100 ? `$${Math.round(x).toLocaleString('en-US')}` : `$${x.toFixed(2)}`)
const pct = (x: number) => `${Math.round(100 * Math.min(1, Math.max(0, x)))}%`
const length = (pieces: Piece[]) => pieces.reduce((sum, p) => sum + p.text.length, 0)

// Props for a Text from a Piece, with no undefined values in them.
function style(p: Piece): { color?: string; bold?: true; dimColor?: true } {
  const out: { color?: string; bold?: true; dimColor?: true } = {}
  if (p.color) out.color = p.color
  if (p.bold) out.bold = true
  if (p.dim) out.dimColor = true
  return out
}

async function launch($: Api): Promise<string[]> {
  if (launcher) return launcher
  const home = (await $.env.get('HOME')) ?? ''
  for (const path of [`${$.plugin.root}/bin/jev`, `${home}/.local/bin/jev`]) {
    if (await $.fs.exists(path)) return (launcher = ['python3', path])
  }
  return (launcher = ['jev'])
}

async function jev($: Api, args: string[], stdin?: string, timeoutMs = 20_000) {
  const argv = [...(await launch($)), ...args]
  const env: Record<string, string> = { ...optionEnv, JEV_SESSION: `session:${sessionId.slice(0, 8)}` }
  const path = await $.env.get('PATH')
  if (path) env.PATH = path
  try {
    return await $.process.run(argv, { stdin, timeoutMs, env, cwd })
  } catch {
    return null
  }
}

function commandOf(input: unknown): string {
  return String((input as { command?: unknown } | null)?.command ?? '').trim()
}

function lastLine(stdout: string | undefined): string {
  return (stdout ?? '').trim().split('\n').pop() ?? ''
}

// At most one refresh in flight and one queued behind it, however many tool calls finish meanwhile.
async function refresh($: Api) {
  if (refreshing) {
    refreshAgain = true
    return
  }
  refreshing = true
  try {
    await refreshNow($)
  } catch {
    // the session ended or the module reloaded mid-refresh: nothing to draw any more
  } finally {
    refreshing = false
  }
  if (refreshAgain) {
    refreshAgain = false
    void refresh($)
  }
}

async function refreshNow($: Api) {
  const before = summary
  const r = await jev($, ['session', '--json', '--compact', '--session', sessionId, '--cwd', cwd])
  if (r && r.exitCode === 0) {
    try {
      summary = JSON.parse(r.stdout) as Summary
    } catch {
      // a partial line: keep the previous numbers
    }
  }
  try {
    const u = await $.session.usage()
    ctxPct = u.context.percent !== undefined ? Math.round(u.context.percent) : null
    sessionUsd = u.cost?.usd ?? null
  } catch {
    ctxPct = null
    sessionUsd = null
  }
  const now = new Date(await $.clock.now())
  refreshedAt = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`
  const trimmed = (summary?.jev.trimmed ?? 0) - (before?.jev.trimmed ?? 0)
  if (before && trimmed > 0) $.ui.log(`jev trimmed ~${k(trimmed)} tokens of that output; the full text is on disk`)
  $.ui.invalidate('ui.render')
}

async function openPane($: Api) {
  paneOpen = true
  await $.ui.open({ id: PANE, title: 'jev', closeOnEscape: true })
  void refresh($)
}

// The line above the prompt never disappears by a press: hide folds it to a chip that brings it back.
function setCollapsed($: Api, value: boolean) {
  collapsed = value
  $.store.set('band_collapsed', value).catch(() => undefined)
  $.ui.invalidate('ui.render')
}

function spent(): { usd: number; estimated: boolean } | null {
  if (sessionUsd !== null && sessionUsd > 0) return { usd: sessionUsd, estimated: false }
  const usd = summary?.model?.usd
  return usd && usd > 0 ? { usd, estimated: true } : null
}

function isIdle(j: JevSide | undefined): boolean {
  return !j || (!j.requests && !j.asked && !j.trimmed)
}

// The band's groups in the order they read, each with a rank: the lowest ranks go first when it is narrow.
function segments(): Segment[] {
  const j = summary?.jev
  const out: Segment[] = []
  if (isIdle(j) || !j) {
    out.push({ key: 'idle', rank: 9, pieces: [{ text: 'ready', color: GOOD }, { text: ' · nothing decided yet this session', dim: true }] })
  } else {
    out.push({ key: 'decisions', rank: 6, pieces: [{ text: n(j.decisions), bold: true }, { text: ' decisions', dim: true }] })
    out.push({ key: 'kept', rank: 5, pieces: [{ text: k(j.kept_out), bold: true, color: INFO }, { text: ' tokens kept out', dim: true }] })
    out.push({ key: 'saved', rank: 9, pieces: [{ text: `~${money(j.saved)}`, bold: true, color: GOOD }, { text: ' saved', dim: true }] })
    const s = spent()
    if (s) out.push({ key: 'share', rank: 4, pieces: [{ text: pct(j.saved / s.usd), color: GOOD }, { text: ' of the session', dim: true }] })
    if (j.trimmed) out.push({ key: 'trimmed', rank: 2, pieces: [{ text: k(j.trimmed), color: INFO }, { text: ' trimmed', dim: true }] })
    if (j.asked) out.push({ key: 'asked', rank: 7, pieces: [{ text: `guard asked ${j.asked}×`, color: WARN }] })
  }
  if (ctxPct !== null) {
    const tone: Piece = ctxPct >= 80 ? { text: `${ctxPct}%`, color: BAD, bold: true } : ctxPct >= 60 ? { text: `${ctxPct}%`, color: WARN } : { text: `${ctxPct}%`, dim: true }
    out.push({ key: 'ctx', rank: 3, pieces: [{ text: 'ctx ', dim: true }, tone] })
  }
  return out
}

function fit(segs: Segment[], room: number): Segment[] {
  const kept = [...segs]
  const width = () => kept.reduce((sum, s) => sum + length(s.pieces), 0) + 3 * Math.max(0, kept.length - 1)
  while (kept.length > 1 && width() > room) {
    let low = 0
    for (let i = 1; i < kept.length; i++) if (kept[i].rank < kept[low].rank) low = i
    kept.splice(low, 1)
  }
  return kept
}

async function judge($: Api, command: string): Promise<Judge | null> {
  const payload = { tool_name: 'Bash', tool_input: { command }, cwd, session_id: sessionId, request: lastPrompt, judge: true }
  const r = await jev($, ['hook', 'guard'], JSON.stringify(payload), 15_000)
  const last = lastLine(r?.stdout)
  if (!r || r.exitCode !== 0 || !last) return null
  try {
    return JSON.parse(last) as Judge
  } catch {
    return null
  }
}

async function rate($: Api, prompt: string): Promise<Route | null> {
  const r = await jev($, ['hook', 'route'], JSON.stringify({ prompt, session_id: sessionId, judge: true }), 8_000)
  const last = lastLine(r?.stdout)
  if (!r || r.exitCode !== 0 || !last) return null
  try {
    const read = JSON.parse(last) as { routine: boolean; name: string; conf: number }
    if (!read.routine || read.conf < cfg.routeConf) return null
    return { kind: cfg.routeMode === 'model' ? 'model' : 'effort', model: cfg.routeModel, name: read.name, conf: read.conf }
  } catch {
    return null
  }
}

function question(command: string, j: Judge): string {
  const shown = command.length > 160 ? `${command.slice(0, 157)}…` : command
  if (j.why === 'project-rule') return `\`${shown}\` matches a rule in .jev/guard.json. Run it?`
  const why: string[] = [`looks destructive (p ${(j.p ?? 0).toFixed(2)})`]
  if ((j.outside ?? 0) >= 0.5) why.push(`reaches outside the project (p ${(j.outside ?? 0).toFixed(2)})`)
  if (j.requested !== null && j.requested !== undefined && j.requested < 0.5) why.push(`does not look like part of what you asked (p ${j.requested.toFixed(2)})`)
  return `\`${shown}\` ${why.join(', ')}. Run it?`
}

function inspectUses(e: { name: string; tier: string; uses: { events: readonly string[]; calls: readonly string[]; env?: { reads: readonly string[]; writes: readonly string[] } } }): string | null {
  const reads = (e.uses.env?.reads ?? []).filter(name => CREDENTIAL.test(name))
  const calls = new Set(e.uses.calls)
  const events = new Set(e.uses.events)
  const notes: string[] = []
  if (reads.length && (calls.has('http.fetch') || calls.has('process.run') || calls.has('process.spawn'))) {
    notes.push(`reads ${reads.map(x => `$${x}`).join(', ')} and reaches ${calls.has('http.fetch') ? 'the network' : 'processes'}`)
  }
  if (events.has('tool.check')) notes.push('can answer permission checks')
  if (calls.has('env.set') || (e.uses.env?.writes ?? []).length) notes.push('writes environment variables')
  if (calls.has('settings.read') && calls.has('fs.write')) notes.push('reads settings and writes files')
  return notes.length ? `jev inspect: mod ${e.name} (${e.tier}) ${notes.join('; ')} — worth a look before relying on it` : null
}

export const register: Register = (on, options) => {
  const opt = (key: string, fallback: string) => {
    const v = options[key]
    return v === undefined || v === '' ? fallback : String(v)
  }
  cfg = {
    guardMode: opt('guard_mode', 'ask'), // ask · strict · deny · off
    bandMode: opt('band_mode', 'on'),
    evidenceLine: opt('evidence_line', 'on'),
    routeMode: opt('route_mode', 'off'), // off · hint · effort · model
    routeModel: opt('route_model', 'haiku'),
    routeConf: Number(opt('route_conf', '0.80')) || 0.8,
  }
  // The plugin's options reach the jev process the way Claude Code hands them to a hook command.
  optionEnv = {}
  for (const [key, value] of Object.entries(options)) optionEnv[`CLAUDE_PLUGIN_OPTION_${key.toUpperCase()}`] = String(value)
  // A load starts from nothing, whatever an earlier load of this module left behind.
  sessionId = ''
  cwd = ''
  canAsk = false
  launcher = null
  summary = null
  ctxPct = null
  sessionUsd = null
  refreshedAt = ''
  collapsed = false
  paneOpen = false
  lastPrompt = ''
  turnCommands = []
  pendingRoute = null
  routeFor.clear()
  flagged.clear()
  refreshing = false
  refreshAgain = false

  // ------------------------------------------------------------------ session

  on('session.start', async ($, e, next) => {
    sessionId = await $.session.id()
    cwd = e.cwd
    canAsk = e.isInteractive && e.surface !== null
    if (canAsk && (cfg.guardMode === 'ask' || cfg.guardMode === 'strict')) {
      // Tells the PreToolUse guard that, where no prompt can appear, this module asks instead of denying.
      await $.env.set('JEV_GUARD_MOD', '1')
    }
    try {
      // An earlier build hid the line for good, with no way back: whoever pressed that gets it back, open.
      if ((await $.store.get('band_hidden')) !== undefined) await $.store.delete('band_hidden')
      collapsed = (await $.store.get('band_collapsed')) === true
    } catch {
      collapsed = false
    }
    // `/jevmate`, or `/jev-session` where something else already has that name; the band's details button
    // and /jevmate:stats show the same numbers either way.
    for (const name of COMMANDS) {
      try {
        await $.command.register({
          name,
          description: 'jev this session in a pane; show or hide the line above the prompt',
          argumentHint: '[show|hide]',
          immediate: true,
        })
        break
      } catch {
        // taken: try the next name
      }
    }
    void refresh($)
    return next(e)
  })

  on('command.run', { command: [...COMMANDS] }, async ($, e) => {
    const arg = String(e.args ?? '').trim().toLowerCase()
    if (arg === 'show' || arg === 'hide') {
      setCollapsed($, arg === 'hide')
      $.ui.toast(arg === 'show' ? 'jev: the line above the prompt is back' : 'jev: line folded · /jevmate show brings it back')
      return {}
    }
    await openPane($)
    return {}
  })

  on('ui.close', async ($, e, next) => {
    if ((e as { id?: string }).id === PANE) paneOpen = false
    return next(e)
  })

  // ------------------------------------------------------------------ the band and the pane

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (cfg.bandMode === 'off' || e.props.hasSurvey) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    const theirs = await next(e) // what other mods draw here stays, under ours
    const j = summary?.jev
    if (collapsed) {
      const chip: Piece = isIdle(j) || !j ? { text: ' ready', dim: true } : { text: ` ~${money(j.saved)} saved`, color: GOOD }
      return (
        <Box flexDirection="column">
          <Box flexDirection="row">
            <Text bold color={ACCENT}>◆ jev</Text>
            <Text {...style(chip)}>{chip.text}</Text>
            <Text>  </Text>
            <Button key="show" label="show" plain dimColor onPress={() => setCollapsed($, false)} />
          </Box>
          {theirs}
        </Box>
      )
    }
    const room = Math.max(24, e.props.bodyColumns - 26)
    const pieces: Piece[] = []
    fit(segments(), room).forEach((seg, i) => {
      if (i) pieces.push({ text: ' · ', dim: true })
      pieces.push(...seg.pieces)
    })
    return (
      <Box flexDirection="column">
        <Box flexDirection="row">
          <Text bold color={ACCENT}>◆ jev</Text>
          <Text>  </Text>
          {pieces.map(p => (
            <Text {...style(p)}>{p.text}</Text>
          ))}
          <Text>   </Text>
          <Button key="details" label="details" plain onPress={() => void openPane($).catch(() => undefined)} />
          <Text dimColor> · </Text>
          <Button key="hide" label="hide" plain dimColor onPress={() => setCollapsed($, true)} />
        </Box>
        {theirs}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const j = summary?.jev
    const project = summary?.cwd ? summary.cwd.split('/').filter(Boolean).pop() : ''
    const row = (label: string, pieces: Piece[]) => (
      <Box flexDirection="row">
        <Box width={LABEL}>
          <Text dimColor>{label}</Text>
        </Box>
        {pieces.map(p => (
          <Text {...style(p)}>{p.text}</Text>
        ))}
      </Box>
    )
    const header = (
      <Box flexDirection="row">
        <Text bold color={ACCENT}>◆ jev</Text>
        <Text dimColor>{` · this session${project ? ` · ${project}` : ''}${refreshedAt ? ` · ${refreshedAt}` : ''}`}</Text>
      </Box>
    )
    const controls = (
      <Box flexDirection="row" columnGap={2}>
        <Button key="toggle" label={collapsed ? 'show the line above the prompt' : 'fold the line above the prompt'} onPress={() => setCollapsed($, !collapsed)} />
        <Button key="refresh" label="refresh" onPress={() => void refresh($)} />
        <Button key="close" label="close" role="dismiss" onPress={() => void $.ui.close({ id: PANE }).catch(() => undefined)} />
      </Box>
    )
    if (isIdle(j) || !j) {
      return (
        <Box flexDirection="column">
          {header}
          <Text> </Text>
          <Text dimColor>{summary ? 'Nothing went through jev yet this session. `jev sift`, `jev tests` and the hooks show up here.' : 'reading the session…'}</Text>
          <Text> </Text>
          {controls}
        </Box>
      )
    }
    const s = spent()
    const share = s ? Math.min(1, j.saved / s.usd) : null
    const bar = Math.max(10, Math.min(36, e.props.bodyColumns - LABEL - 8))
    const filled = share === null ? 0 : Math.round(share * bar)
    const top = Object.entries(j.labels ?? {})
      .slice(0, 4)
      .map(([name, tokens]) => `${name} ${k(tokens)}`)
      .join(' · ')
    return (
      <Box flexDirection="column">
        {header}
        <Text> </Text>
        {row('went through jev', [
          { text: `${n(j.decisions)} decisions`, bold: true },
          { text: ` over ${k(j.tokens)} tokens · ${n(j.requests)} request${j.requests === 1 ? '' : 's'}${j.cached ? `, ${j.cached} from cache` : ''}`, dim: true },
        ])}
        {j.trimmed
          ? row('trimmed', [
              { text: `${k(j.trimmed)} tokens`, bold: true, color: INFO },
              { text: ` of command output kept out in ${j.trim_runs} run${j.trim_runs === 1 ? '' : 's'}`, dim: true },
            ])
          : row('trimmed', [{ text: 'nothing long enough yet', dim: true }])}
        {row('would have cost', [
          { text: `~${money(j.would)}`, bold: true, color: WARN },
          { text: j.once !== undefined ? ` to read it all yourself: ${money(j.once)} once, ${money(j.reread ?? 0)} re-read later` : ' to read it all yourself', dim: true },
        ])}
        {row('saved', [
          { text: `~${money(j.saved)}`, bold: true, color: GOOD },
          { text: ` at most, after ${money(j.paid)} paid to jev`, dim: true },
        ])}
        {share !== null && s
          ? row('', [
              { text: '█'.repeat(filled), color: GOOD },
              { text: '░'.repeat(bar - filled), dim: true },
              { text: ` ${pct(share)}`, bold: true, color: GOOD },
              { text: ` of the session's ${s.estimated ? '~' : ''}${money(s.usd)}`, dim: true },
            ])
          : row('', [{ text: 'the session cost is not known yet', dim: true }])}
        {row('guard', j.asked ? [{ text: `asked ${j.asked}×`, bold: true, color: WARN }, { text: ' this session', dim: true }] : [{ text: 'nothing to ask about yet', dim: true }])}
        {ctxPct !== null
          ? row('context', [{ text: `${ctxPct}%`, bold: true, ...(ctxPct >= 80 ? { color: BAD } : ctxPct >= 60 ? { color: WARN } : {}) }, { text: ' in use', dim: true }])
          : row('context', [{ text: 'unknown', dim: true }])}
        {top ? row('by command', [{ text: top, dim: true }]) : row('by command', [{ text: '—', dim: true }])}
        <Text> </Text>
        {controls}
        <Text> </Text>
        <Text dimColor>saved is a ceiling: the band jev was unsure about was read anyway · /jevmate:stats puts this in the chat</Text>
      </Box>
    )
  })

  // ------------------------------------------------------------------ turns: commands, evidence, routing

  on('turn.start', async ($, e, next) => {
    turnCommands = []
    const route = pendingRoute
    pendingRoute = null
    if (route) {
      routeFor.set(e.turnId, route)
      $.ui.log(`jev route: this prompt reads as ${route.name} (confidence ${route.conf.toFixed(2)}) → ${route.kind === 'effort' ? 'effort low' : `model ${route.model}`}`)
    }
    return next(e)
  })

  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    if (!e.agentId) turnCommands.push(e.command)
    const ran = await next(e)
    void refresh($)
    return ran
  })

  on('turn.complete', async ($, e, next) => {
    const result = await next(e)
    if (e.agentId || e.isAborted) return result
    routeFor.delete(e.turnId)
    void refresh($)
    if (cfg.evidenceLine !== 'off' && e.answer.length >= 40 && CLAIM.test(e.answer) && !turnCommands.some(c => RUNNER.test(c))) {
      return { ...result, text: '⚠ jev: this reply says a test, build or check passed, but no test, build or lint command ran this turn' }
    }
    return result
  })

  on('prompt.submit', async ($, e, next) => {
    if ((e.origin?.kind ?? 'composer') !== 'composer') return next(e) // only what the person typed
    const text = e.text.trim()
    lastPrompt = text.slice(0, 2000)
    if ((cfg.routeMode === 'effort' || cfg.routeMode === 'model') && text.length >= 40 && !/^(\/|\[Image:|@"|@\/)/.test(text)) {
      pendingRoute = await rate($, text)
    }
    return next(e)
  })

  on('turn.step', async function* ($, e, next) {
    const route = routeFor.get(e.turnId)
    if (!route || e.agentId) return yield* next(e)
    return yield* next(route.kind === 'effort' ? { ...e, effort: 'low' as const } : { ...e, model: route.model })
  })

  // ------------------------------------------------------------------ the guard, where no prompt can appear

  // The PreToolUse hook (Python) knows the permission mode. Where no prompt can appear it holds its verdict
  // for this call instead of denying, and the judge below reads it back: no second model call.
  on('tool.check', { tool: 'Bash' }, async ($, e, next) => {
    const decided = await next(e)
    if (!canAsk || cfg.guardMode === 'off' || cfg.guardMode === 'deny' || decided.decision === 'deny') return decided
    const command = commandOf(e.input)
    if (!command || (SAFE.test(command) && !RISKY.test(command))) return decided
    const j = await judge($, command)
    if (!j || j.decision !== 'ask') return decided
    flagged.add(command)
    let answer: string
    try {
      // Refuse first: a dialog that resolves on its own (the person away from the keyboard) must not run it.
      answer = await $.ui.ask(question(command, j), { header: 'jev guard', options: ['Refuse', 'Run it'] })
    } catch {
      return { decision: 'deny', reason: 'jev guard: the question was dismissed; confirm with the person before running this command' }
    } finally {
      flagged.delete(command)
    }
    if (answer === 'Run it') return decided
    return { decision: 'deny', reason: 'jev guard: the person declined this command. Ask before trying a different approach.' }
  }).catch(async ($, e, next) => {
    // A command this hook was about to ask about never runs unasked because the hook failed.
    if (flagged.has(commandOf(e.input))) {
      return { decision: 'deny', reason: 'jev guard could not ask about this command; confirm with the person before running it' }
    }
    return next(e)
  })

  // ------------------------------------------------------------------ other mods, as they load

  on('plugin.register', async ($, e, next) => {
    if (e.name !== $.plugin.name) {
      const note = inspectUses(e)
      if (note) $.ui.log(note)
    }
    return next(e)
  })
}
