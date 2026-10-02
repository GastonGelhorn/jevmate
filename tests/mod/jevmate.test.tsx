// Tests for the mod in hooks/jevmate.tsx: `claude plugin test .` from the repo root.
// The jev process is stood in for: every `$.process.run` gets a canned answer, so nothing leaves the machine.

import { describe, expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const SUMMARY = {
  session: 'abcdef12',
  cwd: '/work/shop',
  model: { usd: 40.0, turns: 120, ctx: 290000, ctx_size: 1000000 },
  jev: {
    requests: 12, decisions: 1234, tokens: 900000, cached: 2, trimmed: 22000, trim_runs: 1, kept_out: 922000,
    paid: 0.0381, would: 18.4, saved: 18.36, asked: 2, once: 9.22, reread: 9.18, share: 0.459,
    labels: { sift: 400000, tests: 300000, 'hook:trim': 22000 },
  },
}

const ok = (stdout: string) => ({ exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false })

type World = { judge?: object; route?: object; calls: string[][]; store: Map<string, unknown> }

const fresh = (entries: Record<string, unknown> = {}): World => ({ calls: [], store: new Map(Object.entries(entries)) })

// Everything beneath the plugin: the engine's answers to what the mod calls, and the events it passes on.
function world(on: On, w: World) {
  mock.clock(on, { now: Date.UTC(2026, 9, 2, 9, 30) })
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
  on('env.set', () => ({ value: undefined }))
  on('session.id', () => ({ value: 'abcdef12-0000-0000' }))
  on('session.usage', () => ({ value: { startedAt: 0, context: { window: 1000000, tokens: 290000, percent: 29 }, rateLimits: [], cost: { usd: 40.0 } } }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.log', () => ({ value: undefined }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.close', () => ({ value: undefined }))
  on('process.run', (_$, e) => {
    w.calls.push([...e.argv])
    if (e.argv.includes('guard')) return { value: ok(JSON.stringify(w.judge ?? { decision: '-', why: 'safe' })) }
    if (e.argv.includes('route')) return { value: ok(JSON.stringify(w.route ?? { routine: false, name: 'hard reasoning', conf: 0.9, level: 3 })) }
    return { value: ok(JSON.stringify(SUMMARY)) }
  })
  // What other mods would draw in the band: it has to stay under ours.
  on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>another mod's band</Text>
  })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('turn.start', (_$, e) => ({ turnId: e.turnId }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  on('prompt.submit', (_$, e) => ({ text: e.text }))
  on('tool.call', { tool: 'Bash' }, () => ({ result: { stdout: '3 passed', stderr: '', interrupted: false } }))
}

const BAND = { hasSurvey: false, isWorking: false, maxRows: 4, bodyColumns: 200, scroll: { offset: 0, bodyRows: 1 }, view: {} }
const PANE_PROPS = { title: 'jev', isFocused: true, bodyColumns: 100, placement: 'dock' as const, scroll: { offset: 0, bodyRows: 30 }, view: {} }

describe('the band above the prompt', () => {
  for (const surface of ['terminal', 'desktop'] as const) {
    test(`shows the session's numbers on the ${surface}, above what other mods draw`, async ($, on) => {
      world(on, fresh())
      await $.session.start({ cwd: '/work/shop', surface, isInteractive: true })
      const band = await $.ui.mount({ plugin: 'jevmate', surface, component: 'AbovePrompt', props: BAND })
      await band.redraw()
      const text = (await band.find({ text: /decisions/ }))?.text ?? ''
      expect(text).toContain('◆ jev')
      expect(text).toContain('1,234 decisions')
      expect(text).toContain('~$18.36 saved')
      expect(text).toContain('46% of the session')
      expect(text).toContain('guard asked 2×')
      expect(await band.find({ text: /another mod's band/ })).toBeDefined()
    })

    test(`hide folds it to a chip on the ${surface}, and show brings it back`, async ($, on) => {
      const w = fresh()
      world(on, w)
      await $.session.start({ cwd: '/work/shop', surface, isInteractive: true })
      const band = await $.ui.mount({ plugin: 'jevmate', surface, component: 'AbovePrompt', props: BAND })
      await band.redraw()
      await band.press({ key: 'hide' })
      expect(await band.find({ text: /decisions/ })).toBeUndefined()
      expect((await band.find({ text: /saved/ }))?.text ?? '').toContain('~$18.36 saved')
      expect(w.store.get('band_collapsed')).toBe(true)
      await band.press({ key: 'show' })
      expect(await band.find({ text: /1,234 decisions/ })).toBeDefined()
      expect(w.store.get('band_collapsed')).toBe(false)
    })
  }

  test('fits a narrow terminal by dropping the least useful numbers first', async ($, on) => {
    world(on, fresh())
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const band = await $.ui.mount({ plugin: 'jevmate', surface: 'terminal', component: 'AbovePrompt', props: { ...BAND, bodyColumns: 80 } })
    await band.redraw()
    const text = (await band.find({ text: /saved/ }))?.text ?? ''
    expect(text).toContain('~$18.36 saved')
    expect(text).not.toContain('trimmed')
  })

  test('a line an earlier build hid comes back open', async ($, on) => {
    const w = fresh({ band_hidden: true })
    world(on, w)
    await $.session.start({ cwd: '/work/shop', surface: 'terminal', isInteractive: true })
    const band = await $.ui.mount({ plugin: 'jevmate', surface: 'terminal', component: 'AbovePrompt', props: BAND })
    await band.redraw()
    expect(await band.find({ text: /1,234 decisions/ })).toBeDefined()
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
    expect(await band.find({ text: /decisions/ })).toBeUndefined()
    expect(await band.find({ text: /another mod's band/ })).toBeDefined()
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
      expect(await pane.find({ text: /went through jev/ })).toBeDefined()
      expect((await pane.find({ text: /~\$18\.40/ }))?.text ?? '').toContain('to read it all yourself')
      expect(await pane.find({ text: /46%/ })).toBeDefined()
      expect(await pane.find({ text: /sift 400k/ })).toBeDefined()
      await pane.press({ key: 'toggle' })
      expect(w.store.get('band_collapsed')).toBe(true)
    })
  }
})

describe('the evidence line', () => {
  test('appears under a claim no command backs, not under one a test run backs', async ($, on) => {
    world(on, fresh())
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })

    await $.turn.start({ text: 'fix the parser', turnId: 't1' })
    const unbacked = await $.turn.complete({ answer: 'Fixed the parser. All tests pass now and the build is green.', durationMs: 900, isAborted: false, turnId: 't1', reason: 'answer' })
    expect(unbacked.text).toContain('no test, build or lint command ran this turn')

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

  test('a read-only command costs nothing', async ($, on) => {
    const w: World = { ...fresh(), judge: RISKY }
    world(on, w)
    on('tool.check', () => ({ decision: 'allow' as const }))
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    expect((await $.tool.check({ tool: 'Bash', input: { command: 'git status' } })).decision).toBe('allow')
    expect(w.calls.some(argv => argv.includes('guard'))).toBe(false)
  })
})

describe('routing', () => {
  test('a routine prompt runs at low effort in effort mode', { options: { route_mode: 'effort' } }, async ($, on) => {
    world(on, { ...fresh(), route: { routine: true, name: 'a routine change', conf: 0.95, level: 1 } })
    let sent: unknown = undefined
    on('turn.step', async function* (_$, e) {
      sent = e.effort
      return { turnId: e.turnId, index: e.index, answer: 'done', toolUses: [], stopReason: 'end_turn', usage: null } as never
    })
    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    await $.prompt.submit({ text: 'rename the helper in utils.py and fix the two call sites, nothing else' })
    await $.turn.start({ text: 'rename the helper', turnId: 'r1' })
    const stream = $.turn.step({ turnId: 'r1', index: 0, model: 'claude-opus-5-5', messageCount: 1 })
    for await (const _chunk of stream) {
      // drain
    }
    expect(sent).toBe('low')
  })
})
