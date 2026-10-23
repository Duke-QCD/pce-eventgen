#!/usr/bin/env python3

import os

# The charge dictionary comes from HIC_CHARGE_DICT, which every runtime profile
# (iS3D paths.sh, the NERSC paths file, osg/container_paths.sh) exports.
charge_data = os.environ.get("HIC_CHARGE_DICT")
if not charge_data:
    raise SystemExit("HIC_CHARGE_DICT is not set; source the iS3D paths helper or your profile")


# Dictionary to store PDG ID and charge mappings
charge_dict = {}

# Read processed data and populate charge_dict
with open(charge_data, 'r') as infile:
    for line in infile:
        # Split the line into PDG ID and charge
        pdg_id, charge = map(int, line.split())
        charge_dict[pdg_id] = charge

def determine_charge_from_pdg_id(pdg_id):
    # Extract the sign from the PDG ID
    sign = 1
    if pdg_id < 0:
        sign = -1
        pdg_id *= -1

    if pdg_id in charge_dict:
        return sign*charge_dict[pdg_id]
    else:
        print("ERROR! INVALID PDG ID: ", pdg_id)
        return None
    

def convert_particle_format(input_file, output_file):
    with open(input_file, 'r') as infile, open(output_file, 'w') as outfile:
        # Write the header line to the output file
        outfile.write("#!OSCAR2013 particle_lists t x y z mass p0 px py pz pdg ID charge\n")
        outfile.write("# Units: fm fm fm fm GeV GeV GeV GeV GeV none none none\n")

        # Initialize ID counter
        particle_id = 0
        count = 0

        # Process each line in the input file
        for line in infile:
            # Skip lines starting with #
            if line.startswith('#'):
                if count > 0:
                    outfile.write("# event " + str(count-1) + " end\n")
                outfile.write("# event " + str(count) + "\n")
                count += 1
                continue
            
            # Split the line into individual values
            values = line.split()

            # Extract values from the original format
            pdg, t, x, y, z, E, px, py, pz = map(float, values)

            pdg = int(pdg)

            # Calculate mass and total momentum (p0)
            mass = (E**2 - px**2 - py**2 - pz**2)**0.5
            p0 = E
            

            charge = determine_charge_from_pdg_id(pdg)

            # Write the values in the new format to the output file
            # Assuming charge information is not available in the input, using '0' as a placeholder
            outfile.write(f"{t} {x} {y} {z} {mass} {p0} {px} {py} {pz} {pdg} {particle_id} {charge}\n")

            # Increment the particle ID
            particle_id += 1
        if count > 0:
            outfile.write("# event " + str(count-1) + " end\n")

input_file_path = 'results/particle_list_osc.dat'
output_file_path = 'results/oscar_list.dat0'

#if len(sys.argv) < 3:
#    print("Usage: python iS3D_OSCAR.py <input_filename> <output_filename>")
#    sys.exit(1)

#input_file_path = sys.argv[1]
#output_file_path = sys.argv[2]

# Call the function to convert the particle format
convert_particle_format(input_file_path, output_file_path)
