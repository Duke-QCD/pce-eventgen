
#include<iostream>
#include<sstream>
#include<string>
#include<fstream>
#include<cmath>
#include<iomanip>
#include<stdlib.h>
#include <gsl/gsl_errno.h>
#include <gsl/gsl_spline.h>
#include <gsl/gsl_interp.h>
#include <cstdlib>
#include <stdexcept>
#include <sstream>

#include "iS3D.h"
#include "deltafReader.h"
#include "ParameterReader.h"
#include "readindata.h"

using namespace std;

namespace {

std::string require_env_path(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || std::string(value).empty()) {
        std::ostringstream msg;
        msg << "Required environment variable " << name << " is not set.";
        throw std::runtime_error(msg.str());
    }
    return std::string(value);
}

std::string join_path(const std::string& dir, const std::string& file) {
    if (dir.empty()) return file;
    if (dir.back() == '/') return dir + file;
    return dir + "/" + file;
}

}


Deltaf_Data::Deltaf_Data(ParameterReader * paraRdr_in)
{
  paraRdr = paraRdr_in;

  hrg_eos = paraRdr->getVal("hrg_eos");
  mode = paraRdr->getVal("mode");
  df_mode = paraRdr->getVal("df_mode");
  include_baryon = paraRdr->getVal("include_baryon");

  if(hrg_eos == 1)
  {
    hrg_eos_path = urqmd;
  }
  else if(hrg_eos == 2)
  {
    hrg_eos_path = smash;
  }
  else if(hrg_eos == 3)
  {
    hrg_eos_path = smash_box;
  }
  else if (hrg_eos == 4)
  {
      hrg_eos_path = smash_equilibrium;
  }
  else if (hrg_eos == 5)
  {
    hrg_eos_path = smash_pce_light;  // single flavor PCE
  }
  else if (hrg_eos == 6)
  {
    hrg_eos_path = smash_pce_light_strange;  // two flavor PCE
  }
  else
  {
    printf("Error: please choose hrg_eos = (1,2,3,4,5,6)\n");
    exit(-1);
  }
}

Deltaf_Data::~Deltaf_Data()
{
  // is there any harm in deallocating memory, while it's being used?
  gsl_spline_free(c0_spline);
  gsl_spline_free(c2_spline);
  gsl_spline_free(c3_spline);

  gsl_spline_free(F_spline);
  gsl_spline_free(betabulk_spline);
  gsl_spline_free(betaV_spline);
  gsl_spline_free(betapi_spline);

  if (lambda_squared_spline != nullptr) gsl_spline_free(lambda_squared_spline);
  if (z_spline != nullptr) gsl_spline_free(z_spline);
}

void Deltaf_Data::load_df_coefficient_data()
{
  printf("Reading in 14 moment and Chapman-Enskog coefficient tables...");

  // Absolute table paths grow with the installation prefix; avoid fixed buffers.
  const std::string c0 = join_path(hrg_eos_path, "c0.dat");
  const std::string c1 = join_path(hrg_eos_path, "c1.dat");
  const std::string c2 = join_path(hrg_eos_path, "c2.dat");
  const std::string c3 = join_path(hrg_eos_path, "c3.dat");
  const std::string c4 = join_path(hrg_eos_path, "c4.dat");
  const std::string F = join_path(hrg_eos_path, "F.dat");
  const std::string G = join_path(hrg_eos_path, "G.dat");
  const std::string betabulk = join_path(hrg_eos_path, "betabulk.dat");
  const std::string betaV = join_path(hrg_eos_path, "betaV.dat");
  const std::string betapi = join_path(hrg_eos_path, "betapi.dat");

  // coefficient files and names
  FILE * c0_file = fopen(c0.c_str(), "r");
  FILE * c1_file = fopen(c1.c_str(), "r");
  FILE * c2_file = fopen(c2.c_str(), "r");
  FILE * c3_file = fopen(c3.c_str(), "r");
  FILE * c4_file = fopen(c4.c_str(), "r");
  FILE * F_file = fopen(F.c_str(), "r");
  FILE * G_file = fopen(G.c_str(), "r");
  FILE * betabulk_file = fopen(betabulk.c_str(), "r");
  FILE * betaV_file = fopen(betaV.c_str(), "r");
  FILE * betapi_file = fopen(betapi.c_str(), "r");

  if(c0_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + c0);
  if(c1_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + c1);
  if(c2_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + c2);
  if(c3_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + c3);
  if(c4_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + c4);

  if(F_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + F);
  if(G_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + G);
  if(betabulk_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + betabulk);
  if(betaV_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + betaV);
  if(betapi_file == NULL) throw std::runtime_error("Cannot open iS3D delta-f table: " + betapi);
  
  // Header dimensions: temperature, then composition (muB or occupancy).
  // All coefficient files share these grid dimensions.
  fscanf(c0_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(c1_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(c2_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(c3_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(c4_file, "%d\n%d\n", &points_T, &composition_points);

  fscanf(F_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(G_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(betabulk_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(betaV_file, "%d\n%d\n", &points_T, &composition_points);
  fscanf(betapi_file, "%d\n%d\n", &points_T, &composition_points);

  //if(!include_baryon) composition_points = 1;

  // skip the header
  char header[300];
  fgets(header, 100, c0_file);
  fgets(header, 100, c1_file);
  fgets(header, 100, c2_file);
  fgets(header, 100, c3_file);
  fgets(header, 100, c4_file);

  fgets(header, 100, F_file);
  fgets(header, 100, G_file);
  fgets(header, 100, betabulk_file);
  fgets(header, 100, betaV_file);
  fgets(header, 100, betapi_file);

  if (points_T <= 0 || points_T > 1000) {
      fprintf(stderr, "Invalid points_T = %d\n", points_T);
      exit(1);
  }
  if (composition_points <= 0 || composition_points > 1000) {
      fprintf(stderr, "Invalid composition_points = %d\n", composition_points);
      exit(1);
  }
  
  // Temperature and shared composition grid.
  T_array = (double *)calloc(points_T, sizeof(double));
  
composition_grid = (double *)calloc(composition_points, sizeof(double));

  c0_data = (double ***)calloc(composition_points, sizeof(double **));
  c1_data = (double ***)calloc(composition_points, sizeof(double **));
  c2_data = (double ***)calloc(composition_points, sizeof(double **));
  c3_data = (double ***)calloc(composition_points, sizeof(double **));
  c4_data = (double ***)calloc(composition_points, sizeof(double **));

  F_data = (double ***)calloc(composition_points, sizeof(double **));
  G_data = (double ***)calloc(composition_points, sizeof(double **));
  betabulk_data = (double ***)calloc(composition_points, sizeof(double **));
  betaV_data = (double ***)calloc(composition_points, sizeof(double **));
  betapi_data = (double ***)calloc(composition_points, sizeof(double **));

  double dummy = 0;
  // PCE tables are ordered [gamma_s][gamma_l][T]; both flavors
  // share the same uniform composition_grid.
  if (hrg_eos == 6)  // Extra dimension for two quark fugacities
  {
    for (int iS = 0; iS < composition_points; iS++) {
      c0_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      c1_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      c2_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      c3_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      c4_data[iS] = (double **)calloc(composition_points, sizeof(double *));

      F_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      G_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      betabulk_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      betaV_data[iS] = (double **)calloc(composition_points, sizeof(double *));
      betapi_data[iS] = (double **)calloc(composition_points, sizeof(double *));

      for (int iB = 0; iB < composition_points; iB++) {
	c0_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
        c1_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
	c2_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
	c3_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
	c4_data[iS][iB] = (double *)calloc(points_T, sizeof(double));

	F_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
	G_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
	betabulk_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
	betaV_data[iS][iB] = (double *)calloc(points_T, sizeof(double));
	betapi_data[iS][iB] = (double *)calloc(points_T, sizeof(double));

	for (int iT = 0; iT < points_T; iT++) {
	  fscanf(c0_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &c0_data[iS][iB][iT]);
         // std::cout << T_array[iT] << " " << composition_grid[iS] << " " << composition_grid[0] << " " << iT << " " << iS << " " << iB << std::endl;
	  fscanf(c1_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &c1_data[iS][iB][iT]);
	  fscanf(c2_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &c2_data[iS][iB][iT]);
	  fscanf(c3_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &c3_data[iS][iB][iT]);
	  fscanf(c4_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &c4_data[iS][iB][iT]);

	  fscanf(F_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &F_data[iS][iB][iT]);
	  fscanf(G_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &G_data[iS][iB][iT]);
	  fscanf(betabulk_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &betabulk_data[iS][iB][iT]);
	  fscanf(betaV_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &betaV_data[iS][iB][iT]);
	  fscanf(betapi_file, "%lf\t\t%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &composition_grid[iS], &betapi_data[iS][iB][iT]);
        }
      }
    }
  }
  else
  {
    // coefficient data
    c0_data[0] = (double **)calloc(composition_points, sizeof(double));
    c1_data[0] = (double **)calloc(composition_points, sizeof(double));
    c2_data[0] = (double **)calloc(composition_points, sizeof(double));
    c3_data[0] = (double **)calloc(composition_points, sizeof(double));
    c4_data[0] = (double **)calloc(composition_points, sizeof(double));

    F_data[0] = (double **)calloc(composition_points, sizeof(double));
    G_data[0] = (double **)calloc(composition_points, sizeof(double));
    betabulk_data[0] = (double **)calloc(composition_points, sizeof(double));
    betaV_data[0] = (double **)calloc(composition_points, sizeof(double));
    betapi_data[0] = (double **)calloc(composition_points, sizeof(double));
    
    // scan coefficient files
    for(int iB = 0; iB < composition_points; iB++)  // muB for equilibrium tables, gamma_l for light PCE
    {
      c0_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      c1_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      c2_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      c3_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      c4_data[0][iB] = (double *)calloc(points_T, sizeof(double));
    
      F_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      G_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      betabulk_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      betaV_data[0][iB] = (double *)calloc(points_T, sizeof(double));
      betapi_data[0][iB] = (double *)calloc(points_T, sizeof(double));
 
      for(int iT = 0; iT < points_T; iT++)  // T
      {
        // Read temperature [GeV] and composition (muB [GeV] or gamma_l).
        fscanf(c0_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &c0_data[0][iB][iT]);
        fscanf(c1_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &c1_data[0][iB][iT]);
        fscanf(c2_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &c2_data[0][iB][iT]);
        fscanf(c3_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &c3_data[0][iB][iT]);
        fscanf(c4_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &c4_data[0][iB][iT]);
      
        fscanf(F_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &F_data[0][iB][iT]);
        fscanf(G_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &G_data[0][iB][iT]);
        fscanf(betabulk_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &betabulk_data[0][iB][iT]);
        fscanf(betaV_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &betaV_data[0][iB][iT]);
        fscanf(betapi_file, "%lf\t\t%lf\t\t%lf\n", &T_array[iT], &composition_grid[iB], &betapi_data[0][iB][iT]);
      } // iT
    } // iB
  } 
  T_min = T_array[0];
  composition_min = composition_grid[0];

  // uniform grid
  dT = fabs(T_array[1] - T_array[0]);
  composition_spacing = fabs(composition_grid[1] - composition_grid[0]);
  

  fclose(c0_file);
  fclose(c1_file);
  fclose(c2_file);
  fclose(c3_file);
  fclose(c4_file);

  fclose(F_file);
  fclose(G_file);
  fclose(betabulk_file);
  fclose(betaV_file);
  fclose(betapi_file);

  printf("done\n");
}


void Deltaf_Data::compute_jonah_coefficients(particle_info * particle_data, int Nparticle) // Needs modification to be used with PCE!
{
  // allocate memory for the arrays
  lambda_squared_array = (double *)calloc(jonah_points, sizeof(double));
  z_array = (double *)calloc(jonah_points, sizeof(double));
  bulkPi_over_Peq_array = (double *)calloc(jonah_points, sizeof(double));

  bulkPi_over_Peq_max = -1.0;      // default to lowest value

  // get the average temperature, energy density, pressure
  Plasma QGP;
  QGP.load_thermodynamic_averages();

  const double T = QGP.temperature;    // GeV (assumes freezeout surface of constant temperature)

  // gauss laguerre roots and weights
  Gauss_Laguerre gla;
  
  gla.load_roots_and_weights(
			     join_path(require_env_path("HIC_IS3D_TABLE_DIR"),
				       "gla_roots_weights_32_points.txt")
			     );
  const int pbar_pts = gla.points;

  double * pbar_root1 = gla.root[1];
  double * pbar_root2 = gla.root[2];

  double * pbar_weight1 = gla.weight[1];
  double * pbar_weight2 = gla.weight[2];

  // calculate the interpolation points of z(bulkPi/P), lambda(bulkPi/P)
  for(int i = 0; i < jonah_points; i++)
  {
    double lambda = lambda_min + (double)i * delta_lambda;

    double E = 0.0;                       // energy density (computed with kinetic theory)
    double P = 0.0;                       // pressure
    double E_mod = 0.0;                   // modified energy density
    double P_mod = 0.0;                   // modified pressure

    // calculate modified energy density (sum over hadron resonance contributions)
    for(int n = 0; n < Nparticle; n++)
    {
      double degeneracy = (double)particle_data[n].gspin;
      double mass = particle_data[n].mass;
      double sign = (double)particle_data[n].sign;
      int mcid = particle_data[n].mc_id;

      double mbar = mass / T;

      if(mass == 0.0) continue;   // I skip the photon (Gamma) because the calculation breaks down for lambda = -1.0

      // ignore common prefactor = pow(T,4) / two_pi2_hbarC3 (since they will cancel out)
      E += degeneracy * Gauss1D_mod(E_mod_int, pbar_root2, pbar_weight2, pbar_pts, mbar, 0.0, sign, 1.0, 1.0, mcid);
      P += (1.0 / 3.0) * degeneracy * Gauss1D_mod(P_mod_int, pbar_root2, pbar_weight2, pbar_pts, mbar, 0.0, sign, 1.0, 1.0, mcid);

      E_mod += degeneracy * Gauss1D_mod(E_mod_int, pbar_root2, pbar_weight2, pbar_pts, mbar, lambda, sign, 1.0, 1.0, mcid);
      P_mod += (1.0 / 3.0) * degeneracy * Gauss1D_mod(P_mod_int, pbar_root2, pbar_weight2, pbar_pts, mbar, lambda, sign, 1.0, 1.0, mcid);
    }

    // jonah's formula (ignoring detLambda factor i.e. n_mod / n -> 1)
    double z = E / E_mod;
    double bulkPi_over_Peq = (P_mod / P) * z  -  1.0;

    // set the arrays and update the max bulk pressure
    lambda_squared_array[i] = lambda * lambda;
    z_array[i] = z;
    bulkPi_over_Peq_array[i] = bulkPi_over_Peq;
    bulkPi_over_Peq_max = max(bulkPi_over_Peq_max, bulkPi_over_Peq);

    //cout << lambda_squared_array[i] << "\t" << z_array[i] << "\t" << bulkPi_over_Peq_array[i] << endl;
  }

  // now construct cubic splines for lambda(bulkPi/Peq) and z(bulkPi/Peq)
  lambda_squared_spline = gsl_spline_alloc(gsl_interp_cspline, jonah_points);
  z_spline = gsl_spline_alloc(gsl_interp_cspline, jonah_points);

  gsl_spline_init(lambda_squared_spline, bulkPi_over_Peq_array, lambda_squared_array, jonah_points);
  gsl_spline_init(z_spline, bulkPi_over_Peq_array, z_array, jonah_points);
}


void Deltaf_Data::construct_cubic_splines()
{
  // Allocate memory for cubic splines
  c0_spline = gsl_spline_alloc(gsl_interp_cspline, points_T);
  c2_spline = gsl_spline_alloc(gsl_interp_cspline, points_T);
  c3_spline = gsl_spline_alloc(gsl_interp_cspline, points_T);

  F_spline = gsl_spline_alloc(gsl_interp_cspline, points_T);
  betabulk_spline = gsl_spline_alloc(gsl_interp_cspline, points_T);
  betapi_spline = gsl_spline_alloc(gsl_interp_cspline, points_T);
  betaV_spline = gsl_spline_alloc(gsl_interp_cspline, points_T);

  // Initialize the cubic splines
  gsl_spline_init(c0_spline, T_array, c0_data[0][0], points_T);
  gsl_spline_init(c2_spline, T_array, c2_data[0][0], points_T);
  gsl_spline_init(c3_spline, T_array, c3_data[0][0], points_T);

  gsl_spline_init(F_spline, T_array, F_data[0][0], points_T);
  gsl_spline_init(betabulk_spline, T_array, betabulk_data[0][0], points_T);
  gsl_spline_init(betaV_spline, T_array, betaV_data[0][0], points_T);
  gsl_spline_init(betapi_spline, T_array, betapi_data[0][0], points_T);
}


deltaf_coefficients Deltaf_Data::cubic_spline(double T, double E, double P, double bulkPi)
{
  deltaf_coefficients df;

  gsl_interp_accel * accel_T = gsl_interp_accel_alloc();    // for temperature dependent functions
  gsl_interp_accel * accel_bulk = gsl_interp_accel_alloc(); // for bulkPi/Peq dependent functions

  switch(df_mode)
  {
    case 1: // 14 moment
    {
      // undo the temperature power scaling of coefficients
      double T4 = T * T * T * T;
      
      df.c0 = gsl_spline_eval(c0_spline, T, accel_T) / T4;
      df.c1 = 0.0;
      df.c2 = gsl_spline_eval(c2_spline, T, accel_T) / T4;
      df.c3 = 0.0;
      df.c4 = 0.0;
      df.shear14_coeff = 2.0 * T * T * (E + P);

      break;
    }
    case 2: // Chapman Enskog
    case 3: // Modified (Mike)
    {
      // undo the temperature power scaling of coefficients
      double T4 = T * T * T * T;

      df.F = gsl_spline_eval(F_spline, T, accel_T) * T;
      df.G = 0.0;
      df.betabulk = gsl_spline_eval(betabulk_spline, T, accel_T) * T4;
      df.betaV = 1.0;
      df.betapi = gsl_spline_eval(betapi_spline, T, accel_T) * T4;

      break;
    }
    case 4: // Modified (Jonah)
    {
      // undo the temperature power scaling of betapi
      double T4 = T * T * T * T;

      double lambda_squared = gsl_spline_eval(lambda_squared_spline, (bulkPi / P), accel_bulk);
      if(bulkPi < 0.0)
      {
        df.lambda = - sqrt(lambda_squared);
      }
      else if(bulkPi > 0.0)
      {
        df.lambda = sqrt(lambda_squared);
      }
      df.z = gsl_spline_eval(z_spline, (bulkPi / P), accel_bulk);
      df.betapi = gsl_spline_eval(betapi_spline, T, accel_T) * T4;
      // linearized correction to lambda, z
      df.delta_lambda = bulkPi / (5.0 * df.betapi -  3.0 * P * (E + P) / E);
      df.delta_z = - 3.0 * df.delta_lambda * P / E;

      break;
    }
    default:
    {
      printf("Error: choose df_mode = (1,2,3,4)\n");
      exit(-1);
    }
  }

  gsl_interp_accel_free(accel_T);
  gsl_interp_accel_free(accel_bulk);

  return df;
}

double Deltaf_Data::calculate_bilinear(double **f_data, double T, double composition, double TL, double TR, double composition_low, double composition_high, int iTL, int iTR, int i_composition_low, int i_composition_high)
{
  // bilinear formula f(T,composition)
  //  f_LR    f_RR
  //
  //  f_LL    f_RL
  
  double f_LL = f_data[i_composition_low][iTL];
  double f_LR = f_data[i_composition_low][iTR];
  double f_RL = f_data[i_composition_high][iTL];
  double f_RR = f_data[i_composition_high][iTR];  // swapped from original code!! - Andrew
 
  return ((f_LL*(TR - T) + f_RL*(T - TL)) * (composition_high - composition)  +  (f_LR*(TR - T) + f_RR*(T - TL)) * (composition - composition_low)) / (dT * composition_spacing);
}

double Deltaf_Data::calculate_trilinear(double ***f_data,
                                        double T, double gamma_l, double gamma_s,
                                        double TL, double TR,
                                        double gamma_l_low, double gamma_l_high,
                                        double gamma_s_low, double gamma_s_high,
                                        int iTL, int iTR,
                                        int i_light_low, int i_light_high,
                                        int i_strange_low, int i_strange_high)
{
    double f000 = f_data[i_strange_low][i_light_low][iTL];
    double f100 = f_data[i_strange_high][i_light_low][iTL];
    double f010 = f_data[i_strange_low][i_light_high][iTL];
    double f110 = f_data[i_strange_high][i_light_high][iTL];

    double f001 = f_data[i_strange_low][i_light_low][iTR];
    double f101 = f_data[i_strange_high][i_light_low][iTR];
    double f011 = f_data[i_strange_low][i_light_high][iTR];
    double f111 = f_data[i_strange_high][i_light_high][iTR];

    double xd = (gamma_s - gamma_s_low) / composition_spacing;
    double yd = (gamma_l - gamma_l_low) / composition_spacing;
    double zd = (T   - TL)   / dT;

    double c00 = f000 * (1 - xd) + f100 * xd;
    double c10 = f010 * (1 - xd) + f110 * xd;
    double c01 = f001 * (1 - xd) + f101 * xd;
    double c11 = f011 * (1 - xd) + f111 * xd;

    double c0 = c00 * (1 - yd) + c10 * yd;
    double c1 = c01 * (1 - yd) + c11 * yd;

    return c0 * (1 - zd) + c1 * zd;
}


deltaf_coefficients Deltaf_Data::bilinear_interpolation(double T, double composition, double E, double P, double bulkPi)
{
  // left and right T, composition indices
  int iTL = (int)floor((T - T_min) / dT);
  int iTR = iTL + 1;
  int i_composition_low = (int)floor((composition - composition_min) / composition_spacing);
  int i_composition_high = i_composition_low + 1;

  double TL, TR, composition_low, composition_high;

  if (i_composition_high == composition_points)   // for fugacity = 1, don't try to interpolate fugacity
  {
      i_composition_high = composition_points - 1;
      i_composition_low = i_composition_low - 1;
  }

  if (iTR == points_T)
  {
      iTR = points_T - 1;
      iTL = iTR - 1;
  }

  if(!(iTL >= 0 && iTR < points_T) || !(i_composition_low >= 0 && i_composition_high < composition_points))
  {
    printf("Error: (T,composition) outside df coefficient table. Exiting...\n");
    std::cout << T << " " << composition << std::endl;
    exit(-1);
  }
  else
  {
    TL = T_array[iTL];
    TR = T_array[iTR];
    composition_low = composition_grid[i_composition_low];
    composition_high = composition_grid[i_composition_high];
  }

  deltaf_coefficients df;

  switch(df_mode)
  {
    case 1:
    {
      double T3 = T * T * T;
      double T4 = T3 * T;
      double T5 = T4 * T;

      // bilinear interpolated values & undo temperature power scaling
      df.c0 = calculate_bilinear(c0_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) / T4;
      df.c1 = calculate_bilinear(c1_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) / T3;
      df.c2 = calculate_bilinear(c2_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) / T4;
      df.c3 = calculate_bilinear(c3_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) / T4;
      df.c4 = calculate_bilinear(c4_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) / T5;
      df.shear14_coeff = 2.0 * T * T * (E + P);

      break;
    }
    case 2:
    case 3:
    {
      double T3 = T * T * T;
      double T4 = T3 * T;

      // bilinear interpolated values & undo temperature power scaling
      df.F = calculate_bilinear(F_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) * T;
      df.G = calculate_bilinear(G_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high);
      df.betabulk = calculate_bilinear(betabulk_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) * T4;
      df.betaV = calculate_bilinear(betaV_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) * T3;
      df.betapi = calculate_bilinear(betapi_data[0], T, composition, TL, TR, composition_low, composition_high, iTL, iTR, i_composition_low, i_composition_high) * T4;

      break;
    }
    case 4:
    {
      printf("Bilinear interpolation error: Jonah df doesn't work for nonzero composition. Exiting..\n");
      exit(-1);
    }
    default:
    {
      printf("Bilinear interpolation error: choose df_mode = (1,2,3). Exiting..\n");
      exit(-1);
    }
  }
  
  return df;
}

deltaf_coefficients Deltaf_Data::trilinear_interpolation(double T, double gamma_l, double gamma_s, double E, double P, double bulkPi)
{
    int iTL = (int)floor((T - T_min) / dT);
    int iTR = iTL + 1;
    int i_light_low = (int)floor((gamma_l - composition_min) / composition_spacing);
    int i_light_high = i_light_low + 1;
    int i_strange_low = (int)floor((gamma_s - composition_min) / composition_spacing);  // Shared occupancy grid for both flavors
    int i_strange_high = i_strange_low + 1;

    if (iTR >= points_T)     { iTR = points_T - 1; iTL = iTR - 1; }
    if (i_light_high >= composition_points) { i_light_high = composition_points - 1; i_light_low = i_light_high - 1; }
    if (i_strange_high >= composition_points) { i_strange_high = composition_points - 1; i_strange_low = i_strange_high - 1; }

    if (!(iTL >= 0 && iTR < points_T) || !(i_light_low >= 0 && i_light_high < composition_points) || !(i_strange_low >= 0 && i_strange_high < composition_points))
    {
        printf("Error: (T, gamma_l, gamma_s) outside df coefficient table. Exiting...\n");
        std::cout << T << " " << gamma_l << " " << gamma_s << std::endl;
        exit(-1);
    }

    double TL   = T_array[iTL];
    double TR   = T_array[iTR];
    double gamma_l_low = composition_grid[i_light_low];
    double gamma_l_high = composition_grid[i_light_high];
    double gamma_s_low = composition_grid[i_strange_low];  // Shared occupancy grid for both flavors
    double gamma_s_high = composition_grid[i_strange_high];

    deltaf_coefficients df;

    switch (df_mode)
    {
        case 1:
        {
            double T3 = T * T * T;
            double T4 = T3 * T;
            double T5 = T4 * T;

            df.c0 = calculate_trilinear(c0_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) / T4;
            df.c1 = calculate_trilinear(c1_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) / T3;
            df.c2 = calculate_trilinear(c2_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) / T4;
            df.c3 = calculate_trilinear(c3_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) / T4;
            df.c4 = calculate_trilinear(c4_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) / T5;
            df.shear14_coeff = 2.0 * T * T * (E + P);
            break;
        }

        case 2:
        case 3:
        {
            double T3 = T * T * T;
            double T4 = T3 * T;

            df.F        = calculate_trilinear(F_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) * T;
            df.G        = calculate_trilinear(G_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high);
            df.betabulk = calculate_trilinear(betabulk_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) * T4;
            df.betaV    = calculate_trilinear(betaV_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) * T3;
            df.betapi   = calculate_trilinear(betapi_data, T, gamma_l, gamma_s, TL, TR, gamma_l_low, gamma_l_high, gamma_s_low, gamma_s_high, iTL, iTR, i_light_low, i_light_high, i_strange_low, i_strange_high) * T4;
            break;
        }

        case 4:
            printf("Trilinear interpolation error: Jonah df doesn't work for nonzero gamma_l. Exiting...\n");
            exit(-1);

        default:
            printf("Trilinear interpolation error: choose df_mode = (1,2,3). Exiting...\n");
            exit(-1);
    }

    return df;
}


deltaf_coefficients Deltaf_Data::evaluate_df_coefficients(double T, double muB, double E, double P, double bulkPi, double gamma_l, double gamma_s)
{
  // PCE: hrg_eos=5 interpolates (T,gamma_l), hrg_eos=6 interpolates
  // (T,gamma_l,gamma_s). These coordinates are not chemical potentials.
  // evaluate the df coefficients by interpolating the data

  deltaf_coefficients df;

  if(!include_baryon and hrg_eos != 2 and hrg_eos != 5 and hrg_eos != 6)
  {
    df = cubic_spline(T, E, P, bulkPi);   // cubic spline interpolation wrt T (at muB = 0)
  }
  else if (hrg_eos == 2 or hrg_eos == 5)
  {
    df = bilinear_interpolation(T, gamma_l, E, P, bulkPi);
  }
  else if (hrg_eos == 6)
  {
    df = trilinear_interpolation(T, gamma_l, gamma_s, E, P, bulkPi);
  }
  else
  {
    // muB on freezeout surface should be nonzero in general
    // otherwise should set include_baryon = 0
    df = bilinear_interpolation(T, muB, E, P, bulkPi);  // bilinear wrt (T, muB)
  }

  return df;
}


void Deltaf_Data::test_df_coefficients(double bulkPi_over_P)
{
  // test print the output of the df coefficients at average temperature, etc (and a fixed value of bulkPi)

  Plasma QGP;
  QGP.load_thermodynamic_averages();

  double E = QGP.energy_density;
  double T = QGP.temperature;
  double P = QGP.pressure;
  double muB = QGP.baryon_chemical_potential;
  double bulkPi = bulkPi_over_P * P;
  double gamma_l = QGP.light_fugacity;
  double gamma_s = QGP.strange_fugacity;

  deltaf_coefficients df;

  df = evaluate_df_coefficients(T, muB, E, P, bulkPi, gamma_l, gamma_s);
  
  if(df_mode == 1)
  {
    printf("\n(c0, c1, c2, c3, c4, shear14) = (%lf, %lf, %lf, %lf, %lf, %lf)\n\n", df.c0, df.c1, df.c2, df.c3, df.c4, df.shear14_coeff);
  }
  else if(df_mode == 2 || df_mode == 3)
  {
    printf("\n(F, G, betabulk, betaV, betapi) = (%lf, %lf, %lf, %lf, %lf)\n\n", df.F, df.G, df.betabulk, df.betaV, df.betapi);
  }
  else if(df_mode == 4)
  {
    printf("\n(lambda, z, dlambda, dz, betapi) = (%lf, %lf, %lf, %lf, %lf)\n\n", df.lambda, df.z, df.delta_lambda, df.delta_z, df.betapi);
  }
}

void Deltaf_Data::compute_particle_densities(particle_info * particle_data, int Nparticle)
{
  // get the average temperature, energy density, pressure, etc.
  Plasma QGP;
  QGP.load_thermodynamic_averages();

  const double T = QGP.temperature;    // GeV
  const double E = QGP.energy_density; // GeV / fm^3
  const double P = QGP.pressure;       // GeV / fm^3
  const double muB = QGP.baryon_chemical_potential;
  const double nB = QGP.net_baryon_density;
  const double gamma_l = QGP.light_fugacity;
  const double gamma_s = QGP.strange_fugacity;
 
  deltaf_coefficients df = evaluate_df_coefficients(T, muB, E, P, 0.0, gamma_l, gamma_s);
 
  double alphaB = muB / T;
  double baryon_enthalpy_ratio = nB / (E + P);

  // gauss laguerre roots and weights
  Gauss_Laguerre gla;
  gla.load_roots_and_weights(
			     join_path(require_env_path("HIC_IS3D_TABLE_DIR"),
				       "gla_roots_weights_32_points.txt")
			     );
  const int pbar_pts = gla.points;

  double * pbar_root1 = gla.root[1];
  double * pbar_root2 = gla.root[2];
  double * pbar_root3 = gla.root[3];

  double * pbar_weight1 = gla.weight[1];
  double * pbar_weight2 = gla.weight[2];
  double * pbar_weight3 = gla.weight[3];

  // calculate the equilibrium densities and the
  // bulk / diffusion corrections of each particle
  for(int i = 0; i < Nparticle; i++)
  {
    double mass = particle_data[i].mass;
    double degeneracy = (double)particle_data[i].gspin;
    double baryon = (double)particle_data[i].baryon;
    double sign = (double)particle_data[i].sign;
    double mbar = mass / T;
    int mcid = particle_data[i].mc_id;

    // equilibrium density
    double neq_fact = degeneracy * pow(T,3) / two_pi2_hbarC3;
    double neq = neq_fact * GaussThermal(neq_int, pbar_root1, pbar_weight1, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);

    // bulk and diffusion density corrections
    double dn_bulk = 0.0;
    double dn_diff = 0.0;
    
    switch(df_mode)
    {
      case 1: // 14 moment (not sure what the status is)
      {
        double c0 = df.c0;
        double c1 = df.c1;
        double c2 = df.c2;
        double c3 = df.c3;
        double c4 = df.c4;

        double J10_fact = degeneracy * pow(T,3) / two_pi2_hbarC3;
        double J20_fact = degeneracy * pow(T,4) / two_pi2_hbarC3;
        double J30_fact = degeneracy * pow(T,5) / two_pi2_hbarC3;
        double J31_fact = degeneracy * pow(T,5) / two_pi2_hbarC3 / 3.0;

        double J10 =  J10_fact * GaussThermal(J10_int, pbar_root1, pbar_weight1, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);
        double J20 = J20_fact * GaussThermal(J20_int, pbar_root2, pbar_weight2, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);
        double J30 = J30_fact * GaussThermal(J30_int, pbar_root3, pbar_weight3, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);
        double J31 = J31_fact * GaussThermal(J31_int, pbar_root3, pbar_weight3, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);

        dn_bulk = ((c0 - c2) * mass * mass * J10 +  c1 * baryon * J20  +  (4.0 * c2 - c0) * J30);
        // these coefficients need to be loaded.
        // c3 ~ cV / V
        // c4 ~ 2cW / V
        dn_diff = baryon * c3 * neq * T  +  c4 * J31;   // not sure if this is right...
        break;
      }
      case 2: // Chapman-Enskog
      case 3: // Modified (Mike)
      {
        double F = df.F;
        double G = df.G;
        double betabulk = df.betabulk;
        double betaV = df.betaV;

        double J10_fact = degeneracy * pow(T,3) / two_pi2_hbarC3;
        double J11_fact = degeneracy * pow(T,3) / two_pi2_hbarC3 / 3.0;
        double J20_fact = degeneracy * pow(T,4) / two_pi2_hbarC3;

        double J10 = J10_fact * GaussThermal(J10_int, pbar_root1, pbar_weight1, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);
        double J11 = J11_fact * GaussThermal(J11_int, pbar_root1, pbar_weight1, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);
        double J20 = J20_fact * GaussThermal(J20_int, pbar_root2, pbar_weight2, pbar_pts, mbar, alphaB, baryon, sign, gamma_l, gamma_s, mcid);

        dn_bulk = (neq + (baryon * J10 * G) + (J20 * F / pow(T,2))) / betabulk;
        dn_diff = (neq * T * baryon_enthalpy_ratio  -  baryon * J11) / betaV;

        break;
      }
      case 4:
      {
        // bulk/diffusion densities not needed for jonah
        break;
      }
      default:
      {
        cout << "Please choose df_mode = (1,2,3,4) in parameters.dat" << endl;
        exit(-1);
      }
    } // df_mode

    particle_data[i].equilibrium_density = neq;
    particle_data[i].bulk_density = dn_bulk;
    particle_data[i].diff_density = dn_diff;
//    std::cout << particle_data[i].name << " " << neq << std::endl;
  }
}








