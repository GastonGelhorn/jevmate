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
  share?: number
}
type Summary = {
  session: string | null
  model: { usd: number; turns: number; ctx: number; ctx_size: number } | null
  jev: JevSide
}
type Judge = {
  decision: string
  why?: string | null
  p?: number
  outside?: number
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
let usageLine = ''
let sessionUsd: number | null = null
let hidden = false
let paneOpen = false
let paneText = ''
let lastPrompt = ''
let turnCommands: string[] = []
let pendingRoute: Route | null = null
const routeFor = new Map<string, Route>()
const flagged = new Set<string>()
let refreshing = false
let refreshAgain = false

const k = (x: number) => (x >= 1e6 ? `${(x / 1e6).toFixed(1)}M` : x >= 1000 ? `${Math.round(x / 1000)}k` : String(x))
const n = (x: number) => x.toLocaleString('en-US')

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

async function refreshPane($: Api) {
  const r = await jev($, ['session', '--plain', '--session', sessionId, '--cwd', cwd])
  if (r && r.exitCode === 0 && r.stdout.trim()) paneText = r.stdout.trimEnd()
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
    usageLine = u.context.percent !== undefined ? ` · ctx ${u.context.percent.toFixed(0)}%` : ''
    sessionUsd = u.cost?.usd ?? null
  } catch {
    usageLine = ''
    sessionUsd = null
  }
  const trimmed = (summary?.jev.trimmed ?? 0) - (before?.jev.trimmed ?? 0)
  if (before && trimmed > 0) $.ui.log(`jev trimmed ~${k(trimmed)} tokens of that output; the full text is on disk`)
  if (paneOpen) await refreshPane($)
  $.ui.invalidate('ui.render')
}

async function openPane($: Api) {
  paneOpen = true
  await refreshPane($)
  await $.ui.open({ id: PANE, title: 'jev session', closeOnEscape: true })
  $.ui.invalidate('ui.render')
}

function hideBand($: Api) {
  hidden = true
  $.store.set('band_hidden', true).catch(() => undefined)
  $.ui.invalidate('ui.render')
}

function bandLine(columns: number): string {
  const j = summary?.jev
  if (!j || (!j.requests && !j.asked && !j.trimmed)) return `jev · nothing decided yet this session${usageLine}`
  const parts = [`jev · ${n(j.decisions)} decisions`, `${k(j.kept_out)} tokens kept out`, `~$${j.would.toFixed(2)} not spent`]
  if (j.trimmed) parts.push(`${k(j.trimmed)} trimmed`)
  if (j.asked) parts.push(`guard asked ${j.asked}×`)
  const spent = sessionUsd ?? summary?.model?.usd ?? null
  if (spent && spent > 0) parts.push(`≈ ${Math.min(999, (100 * j.saved) / spent).toFixed(0)}% of this session's ${sessionUsd === null ? '~' : ''}$${spent.toFixed(2)}`)
  parts.push(`$${j.paid.toFixed(4)} paid`)
  let line = parts.join(' · ') + usageLine
  while (line.length > Math.max(40, columns - 18) && parts.length > 3) {
    parts.pop()
    line = parts.join(' · ') + usageLine
  }
  return line
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
  usageLine = ''
  sessionUsd = null
  hidden = false
  paneOpen = false
  paneText = ''
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
      hidden = (await $.store.get('band_hidden')) === true
    } catch {
      hidden = false
    }
    // `/jevmate`, or `/jev-session` where something else already has that name; the band's details button
    // and /jevmate:stats show the same numbers either way.
    for (const name of COMMANDS) {
      try {
        await $.command.register({
          name,
          description: 'Open the jev pane: what went through jev this session, what it kept out, what that saved',
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

  on('command.run', { command: [...COMMANDS] }, async $ => {
    await openPane($)
    return {}
  })

  on('ui.close', async ($, e, next) => {
    if ((e as { id?: string }).id === PANE) paneOpen = false
    return next(e)
  })

  // ------------------------------------------------------------------ the band and the pane

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (cfg.bandMode === 'off' || hidden || e.props.hasSurvey) return next(e)
    const { Box, Button, Text } = $.ui.resolve(e)
    return (
      <Box>
        <Text dimColor>{bandLine(e.props.bodyColumns)} </Text>
        <Button key="details" label="details" plain onPress={() => void openPane($).catch(() => undefined)} />
        <Text dimColor> </Text>
        <Button key="hide" label="hide" plain onPress={() => hideBand($)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const lines = paneText ? paneText.split('\n') : ['reading the session…']
    return (
      <Box flexDirection="column">
        {lines.map(line => (
          <Text wrap="truncate-end">{line || ' '}</Text>
        ))}
        <Text dimColor> </Text>
        <Text dimColor>/jevmate:stats shows this in the chat · `jev hooks tune` learns the guard's bar from what you let through</Text>
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
      return { ...result, text: 'jev: this reply says a test, build or check passed, but no test, build or lint command ran this turn' }
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
    const question = `${j.reason ?? `jev guard: p(destructive)=${(j.p ?? 0).toFixed(2)} — ${command.slice(0, 160)}`}. Run this command?`
    let answer: string
    try {
      // Refuse first: a dialog that resolves on its own (the person away from the keyboard) must not run it.
      answer = await $.ui.ask(question, ['Refuse', 'Run it'])
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
