#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import numpy as np
import sys

def read_parameters_from_file(file_path, line_number):
    with open(file_path, 'r') as file:
        next(file)
        for i, line in enumerate(file, start=1):
            if i == line_number:
                values = line.strip().split()
                e = float(values[0])
                T = float(values[1])
                fug = float(values[2])
                epT = float(values[3])
                return e, T, fug, epT
    raise ValueError(f"Line {line_number} not found in {file_path}.")

def create_freezeout_surface_data(e, T, fug, epT):
    return np.array([6.0e-01, 0.0e+00, 0.0e+00, 0.0e+00, 1.0e-03, 0.0e+00, 0.0e+00,
                     0.0e+00, 0.0e+00, 0.0e+00, 0.0e+00, 0.0e+00, e, T,
                     0.0e+00, 0.0e+00, 0.0e+00, epT, 0.0e+00, 0.0e+00, 0.0e+00,
                     0.0e+00, 0.0e+00, 0.0e+00, 0.0e+00, 0.0e+00, 0.0e+00, 0.0e+00,
                     fug, 1.0e+00])

def save_to_file(data, file_path):
    np.savetxt(file_path, data.reshape(1, -1), fmt='%.4e')

def process_freezeout_data(input_file, output_file, line_number):
    e, T, fug, epT = read_parameters_from_file(input_file, int(line_number))
    freezeout_data = create_freezeout_surface_data(e, T, fug, epT)
    save_to_file(freezeout_data, output_file)
    print(f"Data saved to {output_file}")

if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python create_1cellsurface.py [input file] [output file] [line_number]")
    else:
        input_path = sys.argv[1]
        output_path = sys.argv[2]
        line_number = sys.argv[3]
        process_freezeout_data(input_path, output_path, line_number)
