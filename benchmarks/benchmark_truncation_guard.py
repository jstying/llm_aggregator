"""Truncation guard benchmark: the real detect_and_truncate() against synthetic degenerate
loop outputs (sentence-level and short-phrase sliding-window repetition), measuring the
size reduction, plus clean realistic responses confirming zero false-positive truncation.
Pure in-memory, no network."""
import _bootstrap  # noqa: F401
import main

DEGENERATE = [
    ('sentence loop', 'The answer is 42. ' * 60),
    ('sentence loop long', ('Concurrency lets independent waits overlap in time. ' * 40)),
    ('short phrase loop', 'go to step 2, ' * 120),
    ('mixed then loop', 'A thread shares memory with its process. ' + 'and so on ' * 90),
]

CLEAN = [
    'A process owns its own address space, while threads inside it share that space and are '
    'scheduled independently by the OS.',
    'Rainbows appear when sunlight is refracted, internally reflected, and dispersed by '
    'water droplets still suspended in the air after rain.',
    'DNS translates human-readable domain names into the numeric IP addresses computers use '
    'to route traffic.',
    'The GIL only serializes Python bytecode execution; blocking socket reads release it, '
    'which is why thread pools still help for network-bound work.',
]


def main_cli():
    reductions = []
    for name, text in DEGENERATE:
        truncated = main.detect_and_truncate(text)
        reduction = (1 - len(truncated) / len(text)) * 100
        reductions.append(reduction)
        print(f'{name}: {len(text)} -> {len(truncated)} chars ({reduction:.1f}% reduction)')
    print(f'average reduction on degenerate outputs: {sum(reductions) / len(reductions):.1f}%')

    false_positives = 0
    for text in CLEAN:
        if main.detect_and_truncate(text) != text:
            false_positives += 1
    print(f'false positives on clean responses: {false_positives}/{len(CLEAN)}')


if __name__ == '__main__':
    main_cli()
