# WARNING(PCE): archived GPU/make workflow. The maintained build is src/cpp
# via CMake; this script and the CUDA implementation have not been adapted to
# the two-flavor PCE runtime/data interface. Use cleanMakeCPU.sh instead.

rm iS3D_GPU.e
cd src
rm iS3D_GPU.e
cd cuda
rm iS3D_GPU.e
rm -r obj
mkdir obj
cd ../../

rm -r results
mkdir results

make iS3D_GPU.e

