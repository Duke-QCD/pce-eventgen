# WARNING(PCE): the copied generator sources still use one occupancy and a
# meson/baryon power rather than the sampler's two-flavor valence composition.
# Do not use this recipe to replace production smash_pce_light_strange tables until the
# generator has been reconciled with hadron_fugacity in src/cpp/gaussThermal.cpp.
####################################################

echo "Calculating df coefficients for Smash Box HRG"

cd legacy_pce_generator/df_vh_dimensionless

echo "removing old smash df coefficients and pdg.dat"
#remove old coefficient tables and pdg file
rm -r legacy_pce_generator
rm pdg.dat

echo "copying in new smash pdg.dat from /PDG"
#copy in new pdg file and rename it as pdg.dat
cp ../../../PDG/pdg_smash_pce.dat .
mv pdg_smash_pce.dat pdg.dat

#make a directory to store the new coefficients
mkdir legacy_pce_generator

echo "compiling"
#clean and compile
#sh cleanMake.sh
rm -r build
mkdir build
cd build 
cmake ..
make 
make install
cd ..

echo "running code to generate vh coeffs"
#run the executable to generate new tables
./deltaf_table

echo "copying new tables to /deltaf_coefficients"
cd ..
#copy the tables back to the deltaf_coeffs directory
cp -R df_vh_dimensionless/legacy_pce_generator ../../deltaf_coefficients/vh/.

# return to original directory
cd ..
