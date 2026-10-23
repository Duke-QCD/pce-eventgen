#!/usr/bin/env bash

# WARNING(PCE): legacy surface_eps_* merger. MUSIC now writes surface.dat,
# so this naming/merge step must be adjusted before use with the PCE workflow.
# The matching old multi-surface job generator also needs adaptation.


folder_name=$1
echo Moving all the results into $folder_name ... 

# combine multiple surface files
for ii in `ls surface_eps_*_*.dat`
do 
    jj=`echo $ii | cut -f 1-3 -d _ `
    cat $ii >> $jj.dat
done
rm surface_eps_*_*.dat 2> /dev/null

mkdir $folder_name
mv *.dat $folder_name
mv *.err $folder_name
mv *.log $folder_name
cp music_input_mode_2 $folder_name/music_input
