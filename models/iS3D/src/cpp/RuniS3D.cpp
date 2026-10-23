#include "iS3D.h"
#include <exception>
#include <iostream>

int main(int argc, char *argv[])
{
  //create an instance of IS3D class
  IS3D particlization;

  //run iS3D
  //if argument == 1, freeeout surface is read from file
  //otherwise freezeout surface is read from memory
  try {
    particlization.run_particlization(1);
  } catch (const std::exception& error) {
    std::cerr << "iS3D: " << error.what() << std::endl;
    return 1;
  }
}
