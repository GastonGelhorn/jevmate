// Tests for the mod in hooks/jevmate.tsx: `claude plugin test .` from the repo root.
// The jev process is stood in for: every `$.process.run` gets a canned answer, so nothing leaves the machine.

import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const JEV = {
  requests: 12, decisions: 1234, tokens: 900000, cached: 2, reads: 700000, overhead: 200000, trimmed: 22000, trim_runs: 1, kept_out: 722000,
  paid: 0.0381, would: 18.4, saved: 18.36, saved_total: 18.36, asked: 2, once: 9.22, reread: 9.18, pricing: 'per-model', share: 0.459,
  labels: { sift: 400000, tests: 300000 }, hook_labels: { guard: 150000, screen: 50000 },
  safety: { asked: 2, pages_flagged: 1, files_flagged: 0, triaged: 0, claims: 0, checks: 340 },
  routing: { subagents: 0, subagent_saved: 0, subagent_spent: 0, effort_turns: 0, effort_cache: null },
}
const SUMMARY = { session: 'abcdef12', cwd: '/work/shop', model: { usd: 40.0, turns: 120, ctx: 290000, ctx_size: 1000000 }, jev: JEV, plan: null }
const SMALL = {
  ...SUMMARY,
  jev: { ...JEV, kept_out: 565, reads: 565, trimmed: 0, would: 0.02, saved: 0.02, saved_total: 0.02, share: 0, labels: { 'mcp:decide': 565 },
         safety: { asked: 28, pages_flagged: 12, files_flagged: 0, triaged: 0, claims: 0, checks: 461 } },
}

const WINDOWS = {
  five_hour: { label: '5-hour window', used: 23.5, resets_at: '2026-10-02T14:00:00Z', as_of: '11:30', rate: 0.5, kept_free: 9.2 },
  seven_day: { label: 'week', used: 12, resets_at: '2026-10-05T09:00:00Z', as_of: '11:30', rate: 0.05, kept_free: 0.92 },
}
const SUBSCRIBED = { ...SUMMARY, plan: { billing: 'subscription', windows: WINDOWS } }
const MEASURING = {
  ...SUMMARY,
  plan: { billing: 'subscription', windows: { five_hour: { ...WINDOWS.five_hour, rate: null, kept_free: null }, seven_day: { ...WINDOWS.seven_day, rate: null, kept_free: null } } },
}
const PLAN_USAGE = {
  startedAt: 0,
  context: { window: 1000000, tokens: 290000, percent: 29 },
  rateLimits: [
    { kind: 'five_hour', percentUsed: 23.5, resetsAt: '2026-10-02T14:00:00Z' },
    { kind: 'seven_day', percentUsed: 12, resetsAt: '2026-10-05T09:00:00Z' },
  ],
  cost: { usd: 40.0 },
}

const ok = (stdout: string) => ({ exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false })

type World = {
  judge?: object
  route?: object
  delegate?: object
  shift?: object
  summary?: object
  usage?: object
  compact?: object
  calls: string[][]
  stdins: string[]
  store: Map<string, unknown>
  env?: string[]
}

const fresh = (entries: Record<string, unknown> = {}): World => ({ calls: [], stdins: [], store: new Map(Object.entries(entries)) })

// Everything beneath the plugin: the engine's answers to what the mod calls, and the events it passes on.
function world(on: On, w: World) {
  const clock = mock.clock(on, { now: Date.UTC(2026, 9, 2, 9, 30) })
  mock.env(on, { HOME: '/home/someone', PATH: '/usr/bin:/bin' })
  on('store.get', (_$, e) => ({ value: w.store.get(e.key) }))
  on('store.set', (_$, e) => {
    w.store.set(e.key, e.value)
    return { value: undefined }
  })
  on('store.delete', (_$, e) => {
    w.store.delete(e.key)
    return { value: undefined }
  })
  on('fs.exists', () => ({ value: true }))
  on('env.set', (_$, e) => {
    w.env?.push(`${e.name}=${e.value}`)
    return { value: undefined }
  })
  on('session.id', () => ({ value: 'abcdef12-0000-0000' }))
  on('session.version', () => ({ value: { version: '2.1.287' } }))
  on('session.usage', () => ({ value: w.usage ?? { startedAt: 0, context: { window: 1000000, tokens: 290000, percent: 29 }, rateLimits: [], cost: { usd: 40.0 } } }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.log', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({ value: undefined }))
  on('process.run', (_$, e) => {
    w.calls.push([...e.argv])
    w.stdins.push(String(e.init?.stdin ?? ''))
    if (e.argv.includes('guard')) return { value: ok(JSON.stringify(w.judge ?? { decision: '-', why: 'safe' })) }
    if (e.argv.includes('route')) return { value: ok(JSON.stringify(w.route ?? { routine: false, name: 'hard reasoning', conf: 0.9, level: 3 })) }
    if (e.argv.includes('delegate')) return { value: ok(JSON.stringify(w.delegate ?? { reading: 0.2 })) }
    if (e.argv.includes('record')) return { value: ok('') }
    if (e.argv.includes('shift')) return { value: ok(JSON.stringify(w.shift ?? { p: 0.1 })) }
    if (e.argv.includes('compact')) return { value: ok(JSON.stringify(w.compact ?? { changes: [] })) }
    return { value: ok(JSON.stringify(w.summary ?? SUMMARY)) }
  })
  // What other mods would draw in the band: it has to stay under ours.
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>another mod's band</Text>
  })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.attach', (_$, e) => ({ clientId: e.clientId }))
  on('turn.start', (_$, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  on('prompt.submit', (_$, e) => ({ text: e.text }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: { stdout: '3 passed', stderr: '', interrupted: false } }))
  return clock
}

const BAND = { hasSurvey: false, isWorking: false, maxRows: 4, bodyColumns: 200, scroll: { offset: 0, bodyRows: 1 }, view: {} }
const PANE_PROPS = { title: 'jev', isFocused: true, bodyColumns: 100, placement: 'dock' as const, scroll: { offset: 0, bodyRows: 30 }, view: {} }

async function bandText($: any, surface: 'terminal' | 'desktop' = 'terminal', columns = 200) {
  const band = await $.ui.mount({ plugin: 'jevmate', surface, component: 'AbovePrompt', props: { ...BAND, bodyColumns: columns } })
  await band.redraw()
  return { band, text: (await band.find({ text: /◆ jev/ }))?.text ?? '' }
}

describe('the band above the prompt', () => {
  for (const surface of ['terminal', 'desktop'] as const) {
    test(`leads with the saving when there is one, on the ${surface}, above what other mods draw`, async ($, on) => {
      world(on, fresh())
      await $.session.start({ cwd: '/work/shop', surface, isInteractive: true })
      const { band, text } = await bandText($, surface)
      expect(text).toContain('~$18.36 saved')
      expect(text).toContain('46% of the session')
      expect(text).toContain('722k tokens kept out')
      expect(text).toContain('guard asked 2×')
      expect(await band.find({ text: /another mod's band/ })).toBeDefined()
    })

    test(`hide folds it to a chip on the ${surface}, and show brings it back`, async ($, on) => {
      const w = fresh()
      world(on, w)
      await $.session.start({ cwd: '/work/shop', surface, isInteractive: true })
      const { band } = await bandText($, surface)
      await band.press({ key: 'hide' })
      expect(await band.find({ text: /tokens kept out/ })).toBeUndefined()
      expect((await band.find({ text: /saved/ }))?.text ?? '').toContain('~$18.36 saved')
      expect(w.store.get('band_collapsed')).toBe(true)
      await band.press({ key: 'show' })
      expect(await band.find({ text: /722k tokens kept out/ })).toBeDefined()
      expect(w.store.get('band_collapsed')).toBe(false)
    })
  }

  test('a saving under 50 cents is not shown as money: what jev caught comes first', async ($, on) => {
    world(on, { ...fresh(), summary: SMALL })
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const { text } = await bandText($)
    expect(text).toContain('guard asked 28×')
    expect(text).toContain('12 pages flagged')
    expect(text).toContain('565 tokens kept out')
    expect(text).not.toContain('$')
  })

  test('fits a narrow terminal by dropping the least useful numbers first', async ($, on) => {
    world(on, fresh())
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const { text } = await bandText($, 'terminal', 80)
    expect(text).toContain('~$18.36 saved')
    expect(text).toContain('guard asked 2×')
    expect(text).not.toContain('ctx')
  })

  test('a line an earlier build hid comes back open', async ($, on) => {
    const w = fresh({ band_hidden: true })
    world(on, w)
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const { text } = await bandText($)
    expect(text).toContain('tokens kept out')
    expect(w.store.has('band_hidden')).toBe(false)
  })

  test('/jevmate hide and /jevmate show fold and unfold it', async ($, on) => {
    const w = fresh()
    world(on, w)
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    await $.command.run({ command: 'jevmate', args: 'hide' })
    expect(w.store.get('band_collapsed')).toBe(true)
    await $.command.run({ command: 'jevmate', args: 'show' })
    expect(w.store.get('band_collapsed')).toBe(false)
  })

  test('is off when band_mode is off', { options: { band_mode: 'off' } }, async ($, on) => {
    world(on, fresh())
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const band = await $.ui.mount({ plugin: 'jevmate', surface: 'terminal', component: 'AbovePrompt', props: BAND })
    expect(await band.find({ text: /◆ jev/ })).toBeUndefined()
    expect(await band.find({ text: /another mod's band/ })).toBeDefined()
  })
})

describe('when Jev cannot judge, and a session where only compaction ran', () => {
  const DOWN = { ...SUMMARY, jev: { ...JEV, api: { reason: 'HTTP 402: Insufficient credits', since: null } } }
  const QUIET = {
    ...SUMMARY,
    jev: { ...JEV, requests: 0, decisions: 0, tokens: 0, cached: 0, reads: 0, overhead: 0, trimmed: 0, trim_runs: 0, kept_out: 0, paid: 0, would: 0,
           saved: 0, saved_total: 0, asked: 0, once: 0, reread: 0, share: 0, labels: {}, hook_labels: {},
           safety: { asked: 0, pages_flagged: 0, files_flagged: 0, triaged: 0, claims: 0, checks: 0 },
           compact: { runs: 1, judged: 77, moved: 1, cut: 0, kept: 76, freed: 900, restored: 2500, rereads: 0, pruned: 0, saved_usd: 0 } },
  }

  test('the band says so first, keeps it on a narrow terminal, and folded too', async ($, on) => {
    world(on, { ...fresh(), summary: DOWN })
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const { band, text } = await bandText($, 'terminal', 80)
    expect(text).toContain("Jev can't judge · no credit")
    expect(text).toContain('~$18.36 saved')
    expect(text).not.toContain('kept out')
    await band.press({ key: 'hide' })
    expect((await band.find({ text: /◆ jev/ }))?.text ?? '').toContain("Jev can't judge")
  })

  test('a narrow band keeps the compactions ahead of the safety figures', async ($, on) => {
    const summary = { ...DOWN, jev: { ...DOWN.jev, compact: { runs: 2, judged: 144, moved: 2, cut: 0, kept: 142, freed: 6186, restored: 4996, rereads: 0, pruned: 0, saved_usd: 0 } } }
    world(on, { ...fresh(), summary })
    await $.session.start({ cwd: '/work/shop', surface: 'desktop', isInteractive: true })
    const { text } = await bandText($, 'desktop', 100)
    expect(text).toContain("Jev can't judge · no credit")
    expect(text).toContain('~$18.36 saved')
    expect(text).toContain('2 compactions')
    expect(text).not.toContain('144 results saved')
  })

  test('the pane says why and what it means', async ($, on) => {
    world(on, { ...fresh(), summary: DOWN })
    await $.session.start({ cwd: '/work/shop', surface: 'desktop', isInteractive: true })
    await $.command.run({ command: 'jevmate', args: '' })
    const pane = await $.ui.mount({ plugin: 'jevmate', surface: 'desktop', component: 'Pane', requestId: 'jev', props: PANE_PROPS })
    await pane.redraw()
    expect((await pane.find({ text: /can't judge/ }))?.text ?? '').toContain('HTTP 402: Insufficient credits · the hooks let everything through unchecked')
  })

  test('compaction counts without a single Jev answer', async ($, on) => {
    world(on, { ...fresh(), summary: QUIET })
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const { text } = await bandText($)
    expect(text).toContain('1 compaction · 77 results saved')
    const narrow = await bandText($, 'desktop', 100)
    expect(narrow.text).toContain('1 compaction')
    expect(text).not.toContain('nothing decided yet')
    expect(text).not.toContain('0 decisions')
    await $.command.run({ command: 'jevmate', args: '' })
    const pane = await $.ui.mount({ plugin: 'jevmate', surface: 'terminal', component: 'Pane', requestId: 'jev', props: PANE_PROPS })
    await pane.redraw()
    expect((await pane.find({ text: /^\s*compaction/ }))?.text ?? '').toContain('1 compaction judged · 1 result moved to disk')
  })
})

describe('the pane', () => {
  for (const surface of ['terminal', 'desktop'] as const) {
    test(`/jevmate opens it with the session table on the ${surface}`, async ($, on) => {
      const w = fresh()
      world(on, w)
      await $.session.start({ cwd: '/work/shop', surface, isInteractive: true })
      await $.command.run({ command: 'jevmate', args: '' })
      const pane = await $.ui.mount({ plugin: 'jevmate', surface, component: 'Pane', requestId: 'jev', props: PANE_PROPS })
      await pane.redraw()
      expect((await pane.find({ text: /^\s*reading/ }))?.text ?? '').toContain('700k tokens judged by jev instead of read')
      expect((await pane.find({ text: /^\s*reading/ }))?.text ?? '').toContain('re-read until the next compaction')
      expect((await pane.find({ text: /^\s*trim/ }))?.text ?? '').toContain('22k tokens of command output kept out in 1 run')
      expect((await pane.find({ text: /^\s*subagents/ }))?.text ?? '').toContain('no subagents this session')
      expect((await pane.find({ text: /^saved/ }))?.text ?? '').toContain("46% of the session's $40.00")
      expect((await pane.find({ text: /^context/ }))?.text ?? '').toContain('in use')
      expect(await pane.find({ text: /sift 400k/ })).toBeDefined()
      expect(await pane.find({ text: /guard 150k/ })).toBeDefined()
      expect((await pane.find({ text: /jev's own cost/ }))?.text ?? '').toContain('nothing from the Claude plan')
      const drawn = JSON.stringify(await pane.drawn())
      if (surface === 'terminal') expect(drawn).toContain('█')
      else expect(drawn).not.toContain('█')
      await pane.press({ key: 'toggle' })
      expect(w.store.get('band_collapsed')).toBe(true)
    })
  }

  test('every lever says whether it is on, and what the subagents could have saved', async ($, on) => {
    const summary = { ...SMALL, model: { ...SUMMARY.model, carry: 0.19, last_model: 'claude-fable-5-1' },
                      subagents: { count: 2, cost: 3.1, models: { 'claude-fable-5-1': 2 }, could_save: 1.25, read_only: 2 } }
    world(on, { ...fresh(), summary })
    await $.session.start({ cwd: '/work/shop', surface: 'desktop', isInteractive: true })
    await $.command.run({ command: 'jevmate', args: '' })
    const pane = await $.ui.mount({ plugin: 'jevmate', surface: 'desktop', component: 'Pane', requestId: 'jev', props: PANE_PROPS })
    await pane.redraw()
    expect((await pane.find({ text: /^\s*subagents/ }))?.text ?? '').toContain('2 subagents ran (2 on fable-5-1) · ~$1.25 less on Sonnet')
    expect((await pane.find({ text: /^\s*low effort/ }))?.text ?? '').toContain('off')
    expect((await pane.find({ text: /^\s*trim/ }))?.text ?? '').toContain('no command output over ~4k tokens yet')
    expect((await pane.find({ text: /^context/ }))?.text ?? '').toContain('each turn re-reads it: ~$0.19 on fable-5-1')
  })

  test('says where jev pays off when the session saved little', async ($, on) => {
    world(on, { ...fresh(), summary: SMALL })
    await $.session.start({ cwd: '/work/shop', surface: 'desktop', isInteractive: true })
    await $.command.run({ command: 'jevmate', args: '' })
    const pane = await $.ui.mount({ plugin: 'jevmate', surface: 'desktop', component: 'Pane', requestId: 'jev', props: PANE_PROPS })
    await pane.redraw()
    expect(await pane.find({ text: /pays off on reading-heavy work/ })).toBeDefined()
    expect((await pane.find({ text: /^safety/ }))?.text ?? '').toContain('12 pages flagged')
  })
})

describe('on a subscription', () => {
  for (const surface of ['terminal', 'desktop'] as const) {
    test(`the band shows the saving as a share of the 5-hour window and of the week on the ${surface}`, async ($, on) => {
      const w: World = { ...fresh(), usage: PLAN_USAGE, summary: SUBSCRIBED }
      world(on, w)
      await $.session.start({ cwd: '/work/shop', surface, isInteractive: true })
      const { text } = await bandText($, surface)
      expect(text).toContain('saved 9.2% of 5h · 0.9% of the week')
      expect(text).not.toContain('$18.36')
      const call = w.calls.find(argv => argv.includes('--plan')) ?? []
      expect(call).toContain('five_hour=23.5@2026-10-02T14:00:00Z')
      expect(call).toContain('seven_day=12@2026-10-05T09:00:00Z')
      expect(call[call.indexOf('--spent') + 1]).toBe('40')
    })

    test(`the pane draws both windows with what jev kept free on the ${surface}`, async ($, on) => {
      world(on, { ...fresh(), usage: PLAN_USAGE, summary: SUBSCRIBED })
      await $.session.start({ cwd: '/work/shop', surface, isInteractive: true })
      await $.command.run({ command: 'jevmate', args: '' })
      const pane = await $.ui.mount({ plugin: 'jevmate', surface, component: 'Pane', requestId: 'jev', props: PANE_PROPS })
      await pane.redraw()
      const five = (await pane.find({ text: /5-hour window/ }))?.text ?? ''
      expect(five).toContain('24% used')
      expect(five).toContain('jev kept 9.2% free')
      expect((await pane.find({ text: /^week/ }))?.text ?? '').toContain('jev kept 0.9% free')
      expect((await pane.find({ text: /API equivalent/ }))?.text ?? '').toContain('not billed per token')
    })
  }

  test('a 5-hour window Claude Code has not read yet says so instead of a blank', async ($, on) => {
    const summary = { ...SUBSCRIBED, plan: { billing: 'subscription', windows: { seven_day: WINDOWS.seven_day }, missing: ['five_hour'] } }
    world(on, { ...fresh(), usage: PLAN_USAGE, summary })
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    await $.command.run({ command: 'jevmate', args: '' })
    const pane = await $.ui.mount({ plugin: 'jevmate', surface: 'terminal', component: 'Pane', requestId: 'jev', props: PANE_PROPS })
    await pane.redraw()
    expect((await pane.find({ text: /^5-hour window/ }))?.text ?? '').toContain('no reading yet')
    expect((await pane.find({ text: /^week/ }))?.text ?? '').toContain('as of 11:30')
    const { text } = await bandText($)
    expect(text).toContain('saved 0.9% of the week')
  })

  test('while the rate is still being learnt the band shows the API equivalent', async ($, on) => {
    world(on, { ...fresh(), usage: PLAN_USAGE, summary: MEASURING })
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const { text } = await bandText($)
    expect(text).toContain('~$18.36 API-equivalent saved')
  })

  test('off a subscription nothing about plans is sent', async ($, on) => {
    const w: World = fresh()
    world(on, w)
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    await bandText($)
    expect(w.calls.some(argv => argv.includes('--plan'))).toBe(false)
  })
})

describe('reading subagents on a cheaper model', () => {
  const SPAWN = { prompt: 'Find every place the outbox is drained and list the files and functions involved.', description: 'find the drains', subagentType: 'general-purpose' }

  test('a reading task runs on Sonnet and its saving is recorded from its own usage', { options: { subagent_model: 'sonnet' } }, async ($, on) => {
    const w: World = { ...fresh(), delegate: { reading: 0.93 } }
    world(on, w)
    let asked = ''
    on('agent.spawn', (_$, e) => {
      asked = String(e.model ?? '')
      return { model: 'claude-sonnet-5-5', agentId: 'agent-1' }
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    await $.agent.spawn(SPAWN)
    expect(asked).toBe('sonnet')
    await $.turn.complete({
      answer: 'The outbox is drained in Engine::drain.', durationMs: 900, isAborted: false, turnId: 's1', reason: 'answer', agentId: 'agent-1',
      usage: { model: 'claude-sonnet-5-5', input_tokens: 2000, output_tokens: 800, cache_read_input_tokens: 90000, cache_creation_input_tokens: 15000 },
    })
    const sent = w.stdins.find(x => x.includes('"hook":"subagent"')) ?? ''
    expect(sent).toContain('"model":"claude-sonnet-5-5"')
    expect(sent).toContain('"cache_read_input_tokens":90000')
  })

  test('a task jev does not read as reading, or a model the caller chose, is left alone', { options: { subagent_model: 'sonnet' } }, async ($, on) => {
    const w: World = { ...fresh(), delegate: { reading: 0.4 } }
    world(on, w)
    const models: string[] = []
    on('agent.spawn', (_$, e) => {
      models.push(String(e.model ?? 'inherit'))
      return { model: 'claude-fable-5-1', agentId: `a${models.length}` }
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    await $.agent.spawn({ ...SPAWN, prompt: 'Design the retry policy for the outbox and implement it.' })
    await $.agent.spawn({ ...SPAWN, model: 'opus' })
    expect(models).toEqual(['inherit', 'opus'])
    expect(w.calls.filter(argv => argv.includes('delegate')).length).toBe(1)
  })

  test('off by default', async ($, on) => {
    const w: World = { ...fresh(), delegate: { reading: 0.99 } }
    world(on, w)
    let asked = 'unset'
    on('agent.spawn', (_$, e) => {
      asked = String(e.model ?? 'inherit')
      return { model: 'claude-fable-5-1', agentId: 'a1' }
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    await $.agent.spawn(SPAWN)
    expect(asked).toBe('inherit')
    expect(w.calls.some(argv => argv.includes('delegate'))).toBe(false)
  })
})

describe('the evidence line', () => {
  test('appears under a claim no command backs, not under one a test run backs', async ($, on) => {
    const w = fresh()
    world(on, w)
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })

    await $.turn.start({ text: 'fix the parser', turnId: 't1' })
    const unbacked = await $.turn.complete({ answer: 'Fixed the parser. All tests pass now and the build is green.', durationMs: 900, isAborted: false, turnId: 't1', reason: 'answer' })
    expect(unbacked.text).toContain('no test, build or lint command ran this turn')
    expect(w.stdins.some(x => x.includes('"hook":"evidence"'))).toBe(true)

    await $.turn.start({ text: 'fix the parser', turnId: 't2' })
    await $.tool.call({ tool: 'Bash', command: 'pytest -q' })
    const backed = await $.turn.complete({ answer: 'Fixed the parser. All tests pass now and the build is green.', durationMs: 900, isAborted: false, turnId: 't2', reason: 'answer' })
    expect(backed.text).not.toContain('jev:')
  })
})

describe('the guard where no prompt can appear', () => {
  const RISKY = { decision: 'ask', why: null, p: 0.96, outside: 0.91, requested: 0.44, ask_at: 0.6, deny_at: 0.9, reason: 'jev guard: p(destructive)=0.96 — git push --force origin main' }

  test('asks in plain words, and refuses when the person refuses', async ($, on) => {
    world(on, { ...fresh(), judge: RISKY })
    let asked = ''
    on('tool.check', () => ({ decision: 'allow' as const }))
    on('tool.call', { tool: 'AskUserQuestion' }, (_$, e) => {
      asked = `${e.questions[0].header}: ${e.questions[0].question}`
      return { result: { questions: e.questions, answers: { [e.questions[0].question]: 'Refuse' } } }
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    const verdict = await $.tool.check({ tool: 'Bash', input: { command: 'git push --force origin main' } })
    expect(verdict.decision).toBe('deny')
    expect(asked).toContain('jev guard')
    expect(asked).toContain('looks destructive (p 0.96)')
    expect(asked).toContain('reaches outside the project (p 0.91)')
    expect(asked).toContain('does not look like part of what you asked (p 0.44)')
  })

  test('lets it through when the person says run it, and never answers allow on its own', async ($, on) => {
    world(on, { ...fresh(), judge: RISKY })
    on('tool.check', (_$, e) => ({ decision: String((e.input as { command: string }).command).startsWith('rm') ? ('ask' as const) : ('allow' as const) }))
    on('tool.call', { tool: 'AskUserQuestion' }, (_$, e) => ({ result: { questions: e.questions, answers: { [e.questions[0].question]: 'Run it' } } }))
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    expect((await $.tool.check({ tool: 'Bash', input: { command: 'git push --force origin main' } })).decision).toBe('allow')
    // what the rules decided as ask stays ask: the mod only ever narrows
    expect((await $.tool.check({ tool: 'Bash', input: { command: 'rm -rf build' } })).decision).toBe('ask')
  })

  test('a dismissed question refuses the command', async ($, on) => {
    world(on, { ...fresh(), judge: RISKY })
    on('tool.check', () => ({ decision: 'allow' as const }))
    on('tool.call', { tool: 'AskUserQuestion' }, () => ({ deny: 'dismissed' }))
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    expect((await $.tool.check({ tool: 'Bash', input: { command: 'git push --force origin main' } })).decision).toBe('deny')
  })

  test('in the desktop app: the session starts with nobody at the prompt, the app attaches, and it asks', async ($, on) => {
    const w: World = { ...fresh(), judge: RISKY, env: [] }
    world(on, w)
    // bypass mode: the PreToolUse hook refuses unless the mod said it would ask
    on('tool.check', () => ({ decision: w.env?.length ? ('allow' as const) : ('deny' as const) }))
    let asked = 0
    on('tool.call', { tool: 'AskUserQuestion' }, (_$, e) => {
      asked++
      return { result: { questions: e.questions, answers: { [e.questions[0].question]: 'Refuse' } } }
    })
    await $.session.start({ cwd: '/repo', surface: null, isInteractive: false })
    expect(w.env).toEqual([])
    // a -p run or the SDK on its own: nobody to ask, the hook's refusal stands
    expect((await $.tool.check({ tool: 'Bash', input: { command: 'git push --force origin main' } })).decision).toBe('deny')
    expect(asked).toBe(0)
    await $.session.attach({ surface: 'desktop', clientId: 'desktop:default' })
    expect(w.env).toEqual(['JEV_GUARD_MOD=1'])
    expect((await $.tool.check({ tool: 'Bash', input: { command: 'git push --force origin main' } })).decision).toBe('deny')
    expect(asked).toBe(1)
  })

  test('a read-only command costs nothing', async ($, on) => {
    const w: World = { ...fresh(), judge: RISKY }
    world(on, w)
    on('tool.check', () => ({ decision: 'allow' as const }))
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    expect((await $.tool.check({ tool: 'Bash', input: { command: 'git status' } })).decision).toBe('allow')
    expect(w.calls.some(argv => argv.includes('guard'))).toBe(false)
  })
})

describe('low effort on routine turns', () => {
  const SMALL_CONTEXT = { startedAt: 0, context: { window: 1000000, tokens: 60000, percent: 6 }, rateLimits: [], cost: { usd: 3.0 } }
  const ROUTINE = { routine: true, name: 'a routine change', conf: 0.95, level: 1 }
  const step = (turnId: string, cacheRead: number, cacheWrite: number) =>
    ({ turnId, index: 0, answer: 'done', toolUses: [], stopReason: 'end_turn', usage: { model: 'claude-fable-5-1', input_tokens: 50, output_tokens: 200, cache_read_input_tokens: cacheRead, cache_creation_input_tokens: cacheWrite } }) as never

  async function turn($: any, turnId: string, prompt: string) {
    await $.prompt.submit({ text: prompt })
    await $.turn.start({ text: prompt, turnId })
    for await (const _chunk of $.turn.step({ turnId, index: 0, model: 'claude-fable-5-1', messageCount: 3 })) {
      // drain
    }
    await $.turn.complete({ answer: 'done', durationMs: 10, isAborted: false, turnId, reason: 'answer' })
  }

  test('a routine prompt runs at low effort on a small context', { options: { route_mode: 'effort' } }, async ($, on) => {
    world(on, { ...fresh(), usage: SMALL_CONTEXT, route: ROUTINE })
    const efforts: unknown[] = []
    on('turn.step', async function* (_$, e) {
      efforts.push(e.effort ?? 'default')
      return step(e.turnId, 60000, 0)
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    await turn($, 'r1', 'rename the helper in utils.py and fix the two call sites, nothing else')
    expect(efforts).toEqual(['low'])
  })

  test('is stopped for good when lowering effort re-wrote the cache', { options: { route_mode: 'effort' } }, async ($, on) => {
    const w: World = { ...fresh(), usage: SMALL_CONTEXT, route: { routine: false, name: 'hard reasoning', conf: 0.9, level: 3 } }
    world(on, w)
    const efforts: unknown[] = []
    on('turn.step', async function* (_$, e) {
      efforts.push(e.effort ?? 'default')
      // the first turn reads 60k from the cache; the one at low effort has to write it all again
      return efforts.length === 1 ? step(e.turnId, 60000, 0) : step(e.turnId, 0, 60000)
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    await turn($, 'r0', 'design the retry policy for the outbox and explain the trade-offs between the options')
    w.route = ROUTINE
    await turn($, 'r1', 'rename the helper in utils.py and fix the two call sites, nothing else')
    await turn($, 'r2', 'rename another helper in utils.py and fix its call sites, nothing else')
    expect(efforts).toEqual(['default', 'low', 'default'])
    expect((w.store.get('effort_cache') as { verdict: string }).verdict).toBe('rewrites')
    expect(w.stdins.some(x => x.includes('"hook":"effort-cache"') && x.includes('rewrites'))).toBe(true)
  })

  test('waits for a small context before the first check', { options: { route_mode: 'effort' } }, async ($, on) => {
    world(on, { ...fresh(), route: ROUTINE }) // the default usage reports a 290k context
    const efforts: unknown[] = []
    on('turn.step', async function* (_$, e) {
      efforts.push(e.effort ?? 'default')
      return step(e.turnId, 280000, 0)
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    await turn($, 'r1', 'rename the helper in utils.py and fix the two call sites, nothing else')
    expect(efforts).toEqual(['default'])
  })
})

describe('compaction', () => {
  const BIG = 'x'.repeat(2000)
  const MSGS = [
    { role: 'user', text: 'fix the retry bug in the outbox', toolUses: [], handle: 'h1' },
    { role: 'assistant', text: '', toolUses: [{ tool_use_id: 'u1', tool: 'Bash', input: { command: 'du -sh *' }, text: BIG }], handle: 'h2' },
    { role: 'user', text: '', toolUses: [], toolResults: [{ tool_use_id: 'u1', text: BIG, isError: false }], handle: 'h3' },
    { role: 'assistant', text: 'Found it.', toolUses: [], handle: 'h4' },
  ]
  const STUB = '[jev compact] Bash du -sh *: 1 lines, ~602 tokens, moved out before a compaction. The full text is at /home/someone/.config/jev/compacted/session-abcdef12-1/001-Bash.txt'
  const SUMMARY_MSG = { role: 'user', text: 'what happened so far', toolUses: [] }
  const usage = (cacheRead: number, input: number) => ({ input_tokens: input, output_tokens: 900, cache_read_input_tokens: cacheRead, cache_creation_input_tokens: 0 })
  const start = ($: any) => $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })

  test('off by default: Claude Code summarizes the conversation as it is', async ($, on) => {
    const w = fresh()
    world(on, w)
    let seen: unknown = null
    on('session.compact', (_$, e) => {
      seen = e.messages
      return { messages: [SUMMARY_MSG] }
    })
    await start($)
    await $.session.compact({ trigger: 'manual', messages: MSGS } as never)
    expect(seen).toEqual(MSGS)
    expect(w.calls.some(a => a.includes('compact'))).toBe(false)
  })

  test('learns from the first compaction whether pruning before the summary pays', { options: { compact_mode: 'on' } }, async ($, on) => {
    const w = fresh()
    world(on, w)
    let seen: unknown = null
    on('session.compact', (_$, e) => {
      seen = e.messages
      return { messages: [SUMMARY_MSG], usage: usage(1000, 150000) }
    })
    await start($)
    await $.session.compact({ trigger: 'auto', messages: MSGS } as never)
    expect(seen).toEqual(MSGS)
    expect((w.store.get('compact_cache') as { verdict: string }).verdict).toBe('pays')
    expect(w.stdins.some(x => x.includes('"hook":"compact-cache"') && x.includes('pays'))).toBe(true)
  })

  test('never prunes where the summarizer reads the conversation from the cache', { options: { compact_mode: 'on' } }, async ($, on) => {
    const w = fresh()
    world(on, w)
    on('session.compact', () => ({ messages: [SUMMARY_MSG], usage: usage(140000, 2000) }))
    await start($)
    await $.session.compact({ trigger: 'auto', messages: MSGS } as never)
    expect((w.store.get('compact_cache') as { verdict: string }).verdict).toBe('costs')
  })

  test('once it pays, the summarizer reads the conversation with stale results moved out', { options: { compact_mode: 'on' } }, async ($, on) => {
    const w: World = { ...fresh({ compact_cache: { version: '2.1.287', verdict: 'pays' } }), compact: { changes: [{ id: 'u1', kind: 'result', text: STUB }] } }
    world(on, w)
    let seen: any[] = []
    on('session.compact', (_$, e) => {
      seen = [...e.messages]
      return { messages: [SUMMARY_MSG] }
    })
    await start($)
    await $.session.compact({ trigger: 'auto', messages: MSGS } as never)
    expect(w.calls.some(a => a.includes('compact') && a.includes('--messages') && a.includes('--apply'))).toBe(true)
    expect(seen[0]).toEqual(MSGS[0])
    expect(seen[3]).toEqual(MSGS[3])
    expect(seen[1].handle).toBeUndefined()
    expect(seen[1].toolUses[0].text).toBe(STUB)
    expect(seen[2].toolResults[0].text).toBe(STUB)
    expect(seen[2].toolResults[0].tool_use_id).toBe('u1')
  })

  test('a summary computed ahead of time is skipped while pruning is on', { options: { compact_mode: 'on' } }, async ($, on) => {
    const w = fresh({ compact_cache: { version: '2.1.287', verdict: 'pays' } })
    world(on, w)
    on('session.compact', () => ({ messages: [SUMMARY_MSG] }))
    await start($)
    const result = (await $.session.compact({ trigger: 'precompute', messages: MSGS } as never)) as { skip?: string }
    expect(result.skip).toBeDefined()
  })

  test('a saved result read again is counted', { options: { compact_mode: 'on' } }, async ($, on) => {
    const w = fresh()
    world(on, w)
    on('tool.call', { tool: 'Read' }, () => ({ result: { type: 'text', file: { filePath: 'x', content: 'y', numLines: 1, startLine: 1, totalLines: 1 } } }) as never)
    await start($)
    await $.tool.call({ tool: 'Read', file_path: '/home/someone/.config/jev/compacted/session-abcdef12-1/001-Bash.txt' } as never)
    expect(w.stdins.some(x => x.includes('"hook":"compact-reread"'))).toBe(true)
  })
})

describe('compactions jev starts itself (compact_mode auto), in the background', () => {
  const BIG_CONTEXT = { startedAt: 0, context: { window: 1000000, tokens: 300000, percent: 30 }, rateLimits: [], cost: { usd: 20.0 } }
  const SMALL_CONTEXT = { ...BIG_CONTEXT, context: { window: 1000000, tokens: 60000, percent: 6 } }
  const SUMMARY_MSG = { role: 'user', text: 'what happened so far', toolUses: [] }
  const ANSWER = 'Fixed: the retry now backs off.'
  const step = (turnId: string) =>
    ({ turnId, index: 0, answer: ANSWER, toolUses: [], stopReason: 'end_turn', usage: { model: 'claude-opus-5-5', input_tokens: 50, output_tokens: 200, cache_read_input_tokens: 290000, cache_creation_input_tokens: 0 } }) as never
  type Seen = { trigger?: string; instructions?: string }

  async function turn($: any, turnId: string, prompt: string) {
    await $.prompt.submit({ text: prompt })
    await $.turn.start({ text: prompt, turnId })
    for await (const _chunk of $.turn.step({ turnId, index: 0, model: 'claude-opus-5-5', messageCount: 3 })) {
      // drain
    }
    await $.turn.complete({ answer: ANSWER, durationMs: 10, isAborted: false, turnId, reason: 'answer' })
  }

  function core(on: On, seen: Seen[]) {
    on('turn.step', async function* (_$, e) {
      return step(e.turnId)
    })
    on('session.compact', (_$, e) => {
      seen.push({ trigger: e.trigger, instructions: e.instructions })
      return { messages: [SUMMARY_MSG], tokensBefore: 300000, tokensAfter: 30000, usage: { input_tokens: 2000, output_tokens: 6000, cache_read_input_tokens: 290000, cache_creation_input_tokens: 0 } }
    })
  }

  const start = ($: any) => $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })

  test('idle long enough, it compacts before the prompt cache expires', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const w: World = { ...fresh(), usage: BIG_CONTEXT }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await start($)
    await turn($, 't1', 'fix the retry bug in the outbox and add a test for the backoff')
    await clock.advance(54 * 60_000)
    expect(seen).toEqual([])
    await clock.advance(2 * 60_000)
    expect(seen.length).toBe(1)
    const row = w.stdins.find(x => x.includes('"hook":"compact-auto"')) ?? ''
    expect(row).toContain('"reason":"idle"')
    expect(row).toContain('"before":300000')
    expect(row).toContain('"after":30000')
    expect(row).toContain('"ttl_min":60')
    expect(row).toContain(`"last_at":${Date.UTC(2026, 9, 2, 9, 30)}`)
  })

  test('in the desktop app too, once the app attaches; never in a run nobody watches', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const w: World = { ...fresh(), usage: BIG_CONTEXT }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await $.session.start({ cwd: '/repo', surface: null, isInteractive: false })
    await turn($, 't1', 'fix the retry bug in the outbox and add a test for the backoff')
    await clock.advance(56 * 60_000)
    expect(seen).toEqual([])
    await $.session.attach({ surface: 'desktop', clientId: 'desktop:default' })
    await turn($, 't2', 'and the dead-letter path, does it back off too?')
    await clock.advance(56 * 60_000)
    expect(seen.length).toBe(1)
  })

  test('a prompt before then stops the timer', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const w: World = { ...fresh(), usage: BIG_CONTEXT }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await start($)
    await turn($, 't1', 'fix the retry bug in the outbox and add a test for the backoff')
    await clock.advance(30 * 60_000)
    await $.prompt.submit({ text: 'and the dead-letter path, does it back off too?' })
    await clock.advance(40 * 60_000)
    expect(seen).toEqual([])
  })

  test('after a turn whose prompt started other work, it compacts with what that work needs', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const w: World = { ...fresh(), usage: BIG_CONTEXT, shift: { p: 0.92 } }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await start($)
    await turn($, 't1', 'fix the retry bug in the outbox and add a test for the backoff')
    await clock.advance(2_000)
    expect(w.calls.some(a => a.includes('shift'))).toBe(false)
    await turn($, 't2', 'now build the csv export of the invoices for the billing page')
    expect(seen).toEqual([])
    await clock.advance(2_000)
    expect(seen.length).toBe(1)
    expect(seen[0].instructions).toContain('csv export of the invoices')
    const asked = w.stdins.find(x => x.includes('"asked"')) ?? ''
    expect(asked).toContain('fix the retry bug in the outbox')
    expect(asked).toContain(ANSWER)
    const row = w.stdins.find(x => x.includes('"hook":"compact-auto"')) ?? ''
    expect(row).toContain('"reason":"shift"')
    expect(row).toContain('"p":0.92')
  })

  test('nothing on a small context', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const w: World = { ...fresh(), usage: SMALL_CONTEXT, shift: { p: 0.99 } }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await start($)
    await turn($, 't1', 'fix the retry bug in the outbox and add a test for the backoff')
    await turn($, 't2', 'now build the csv export of the invoices for the billing page')
    await clock.advance(70 * 60_000)
    expect(seen).toEqual([])
    expect(w.calls.some(a => a.includes('shift'))).toBe(false)
  })

  test('nothing with compact_mode on', { options: { compact_mode: 'on' } }, async ($, on) => {
    const w: World = { ...fresh(), usage: BIG_CONTEXT, shift: { p: 0.99 } }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await start($)
    await turn($, 't1', 'fix the retry bug in the outbox and add a test for the backoff')
    await turn($, 't2', 'now build the csv export of the invoices for the billing page')
    await clock.advance(70 * 60_000)
    expect(seen).toEqual([])
  })

  test('the same session after a restart keeps its timer', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const w: World = { ...fresh({ last_request: { session: 'abcdef12-0000-0000', at: Date.UTC(2026, 9, 2, 8, 40) } }), usage: BIG_CONTEXT }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await start($)
    await clock.advance(6 * 60_000)
    expect(seen.length).toBe(1)
  })

  test('and lets it go once the cache has expired', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const w: World = { ...fresh({ last_request: { session: 'abcdef12-0000-0000', at: Date.UTC(2026, 9, 2, 8, 0) } }), usage: BIG_CONTEXT }
    const clock = world(on, w)
    const seen: Seen[] = []
    core(on, seen)
    await start($)
    await clock.advance(10 * 60_000)
    expect(seen).toEqual([])
  })

  test('the line and the pane say what they saved', { options: { compact_mode: 'auto' } }, async ($, on) => {
    const compact = { runs: 2, judged: 144, moved: 2, cut: 0, kept: 142, freed: 6186, restored: 4996, rereads: 0, pruned: 0, saved_usd: 0,
                      auto: { runs: 1, idle: 1, shift: 0, requests: 3, saved_usd: 0.4 } }
    world(on, { ...fresh(), summary: { ...SUMMARY, jev: { ...JEV, compact } } })
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const { text } = await bandText($)
    expect(text).toContain('2 compactions (1 by jev, ~$0.40)')
    await $.command.run({ command: 'jevmate', args: '' })
    const pane = await $.ui.mount({ plugin: 'jevmate', surface: 'terminal', component: 'Pane', requestId: 'jev', props: PANE_PROPS })
    await pane.redraw()
    const line = (await pane.find({ text: /^\s*compaction/ }))?.text ?? ''
    expect(line).toContain('auto')
    expect(line).toContain('1 started by jev (1 while idle): ~$0.40 saved over 3 requests since')
  })
})
