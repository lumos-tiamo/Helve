import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import { Box, render, Static, Text } from "ink";

/**
 * Why this test exists (audit 2026-07-26 P1).
 *
 * Ink appends every `<Static>` write to a private `fullStaticOutput` string and,
 * once the dynamic region fills the terminal, writes
 * `clearTerminal + fullStaticOutput + output` — reprinting the whole accumulated
 * history. Under ink 6 that string was only reset in the constructor, so
 * remounting `<Static>` under a new key (what a `transcriptEpoch` bump does)
 * appended the same entries a second time and the user saw two copies of their
 * history, three after the next bump. `clearTerminal()` clears the screen but
 * cannot reach that cache.
 *
 * ink 7 resets `fullStaticOutput` when the `<Static>` identity changes, which is
 * what makes the shell's epoch-bump pattern safe. The whole pattern rests on
 * that guarantee, so it is pinned here: if a future ink release drops it, this
 * fails and the transcript starts duplicating in production.
 */

const CLEAR_TERMINAL = "[2J";
const ITEMS = ["HISTORY_A", "HISTORY_B"];

type FakeTty = EventEmitter & {
  isTTY: boolean;
  rows: number;
  columns: number;
  writes: string[];
  write(chunk: string): boolean;
};

function fakeTty(rows = 8): FakeTty {
  const out = new EventEmitter() as FakeTty;
  out.isTTY = true;
  out.rows = rows;
  out.columns = 40;
  out.writes = [];
  out.write = (chunk: string) => {
    out.writes.push(String(chunk));
    return true;
  };
  return out;
}

function fakeStdin() {
  const stdin = new EventEmitter();
  Object.assign(stdin, {
    isTTY: true,
    setRawMode() {},
    ref() {},
    unref() {},
    read: () => null,
    setEncoding() {},
    resume() {},
    pause() {},
  });
  return stdin as unknown as NodeJS.ReadStream;
}

/** `tall` grows the dynamic region past `rows`, which arms ink's reprint branch. */
function Harness({ staticKey, tall }: { staticKey: number; tall: boolean }) {
  return (
    <Box flexDirection="column">
      <Static key={`t-${staticKey}`} items={ITEMS}>
        {(item) => <Text key={item}>{item}</Text>}
      </Static>
      <Box flexDirection="column">
        {Array.from({ length: tall ? 14 : 1 }, (_unused, index) => `dyn${index}`).map((line) => (
          <Text key={line}>{line}</Text>
        ))}
      </Box>
    </Box>
  );
}

/**
 * Ink's render is throttled, so a fixed delay is a bet on how loaded the
 * machine is -- one that a CI runner loses.  Every step below waits for the
 * write it expects instead, which is both faster locally and stable on a
 * runner that pauses for half a second in the middle of the test.
 */
async function waitFor(label: string, predicate: () => boolean, timeoutMs = 5000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`timed out after ${timeoutMs}ms waiting for ${label}`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

const historyWrites = (writes: string[]) => writes.filter((write) => write.includes("HISTORY_A")).length;

const reprints = (writes: string[]) =>
  writes.filter((write) => write.includes(CLEAR_TERMINAL) && write.includes("HISTORY_A"));

/** Copies of a history entry contained in each full-history reprint write. */
async function copiesPerReprint(): Promise<number[]> {
  const stdout = fakeTty();
  const instance = render(<Harness staticKey={0} tall={false} />, {
    stdout: stdout as unknown as NodeJS.WriteStream,
    stdin: fakeStdin(),
    exitOnCtrlC: false,
    patchConsole: false,
    // Ink turns off its clear-and-reprint rendering when it detects CI
    // (ink.js: `interactive ?? (!isInCi && stdout.isTTY)`), and the reprint is
    // the whole subject of this test — without this the assertion cannot fire
    // on a runner no matter how long it waits.  Asking for the interactive
    // path explicitly is what makes the guarantee testable everywhere.
    interactive: true,
  });
  await waitFor("the first static write", () => historyWrites(stdout.writes) > 0);

  // The epoch bump: <Static> remounts under a new key, re-emitting every item.
  const beforeBump = historyWrites(stdout.writes);
  instance.rerender(<Harness staticKey={1} tall={false} />);
  await waitFor("the remounted static write", () => historyWrites(stdout.writes) > beforeBump);

  // Fill the terminal so ink reprints its accumulated static output.
  instance.rerender(<Harness staticKey={1} tall={true} />);
  instance.rerender(<Harness staticKey={1} tall={true} />);
  await waitFor("a full-history reprint", () => reprints(stdout.writes).length > 0);

  instance.unmount();
  instance.cleanup();

  return reprints(stdout.writes).map((write) => (write.match(/HISTORY_A/g) ?? []).length);
}

test("ink resets its static cache when Static remounts, so history prints once", async () => {
  const copies = await copiesPerReprint();

  assert.ok(copies.length > 0, "expected at least one full-history reprint");
  assert.deepEqual(
    copies.filter((count) => count !== 1),
    [],
    `every reprint must contain exactly one copy of the history, got ${JSON.stringify(copies)}`,
  );
});
