#!/usr/bin/env python3
"""Check matrix, design, measurements and observable assembly without training or writes."""
import argparse
import inference as I


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    I.add_input_arguments(parser)
    args = parser.parse_args(argv)
    I.configure_inputs(args)
    feeddown = I.feeddown_selection(args)
    try:
        inputs = I.prepare_inputs(drop_specs=I.drop_selection(args), feeddown=feeddown)
        I.resolve_free(inputs.pnames, inputs.ranges)
    except (OSError, KeyError, ValueError, IndexError) as error:
        parser.exit(1, f"Input check failed: {error}\n")
    print(f"PASS: {I.SYSTEM}, profile {I.PROFILE}")
    print(f"Matrix: {I.MATRIX_NPZ}; shape {inputs.raw_shape}")
    print(f"Aligned X: {inputs.X.shape}; {len(inputs.labels)} matched observables; "
          f"{len(inputs.X)} usable designs, {int((~inputs.valid).sum())} excluded by assembly")
    print(f"Active parameters: {len(inputs.pnames)} (all sampled by default)")
    print(f"Excluded observables: {', '.join(I.drop_selection(args)) or 'none'}")
    print(f"Fixed parameters in the system slice (--parameter-mode system): {I.FIXED_PARAMS or 'none'}")
    print(f'Feed-down: {I.FEEDDOWN_NPZ if feeddown is None else feeddown or "off"}')
    print("No emulator training, sampling, or output-file writes performed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
