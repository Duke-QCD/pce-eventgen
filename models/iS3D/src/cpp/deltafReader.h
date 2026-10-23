
#ifndef DELTAFREADER_H
#define DELTAFREADER_H

#include "ParameterReader.h"
#include "readindata.h"
#include "gaussThermal.h"
#include "data_paths.h"
#include <fstream>
#include <gsl/gsl_errno.h>
#include <gsl/gsl_spline.h>
#include <gsl/gsl_interp.h>
#include <cstdlib>
#include <stdexcept>
#include <sstream>

using namespace std;

inline string require_env_path_deltaf(const char* name) {
    const char* value = std::getenv(name);
    if (value == nullptr || string(value).empty()) {
        std::ostringstream msg;
        msg << "Required environment variable " << name << " is not set.";
        throw std::runtime_error(msg.str());
    }
    return string(value);
}

inline string join_path_deltaf(const string& dir, const string& file) {
    if (dir.empty()) return file;
    if (dir.back() == '/') return dir + file;
    return dir + "/" + file;
}


class Deltaf_Data
{
    private:
        ParameterReader * paraRdr;

        int hrg_eos; // type of pdg file for hadron resonance gas EoS
        int mode; //type of freezeout surface, VH or VAH
        int df_mode; // type of delta-f correction (e.g. 14-moment, CE, or modified distribution)
        int include_baryon;

        string hrg_eos_path;
        string deltaf_root = require_env_path_deltaf("HIC_IS3D_DELTAF_DIR");
        string urqmd     = join_path_deltaf(deltaf_root, "urqmd/");
        string smash     = join_path_deltaf(deltaf_root, "smash/");
        string smash_box = join_path_deltaf(deltaf_root, "smash_box/");
        string smash_equilibrium = is3d::data_path(deltaf_root, "smash_equilibrium/", "smash_eq/", "c0.dat");
        string smash_pce_light = is3d::data_path(deltaf_root, "smash_pce_light/", "smash_new/", "c0.dat");
        string smash_pce_light_strange = is3d::data_path(deltaf_root, "smash_pce_light_strange/", "smash_pce/", "c0.dat");
    public:
        int points_T;
        // Shared composition axis: muB in equilibrium tables, gamma_l in
        // light PCE, and the common gamma_l/gamma_s grid in two-flavor PCE.
        int composition_points;

        double T_min;
        double composition_min;

        double dT;
        double composition_spacing;

        double * T_array;
        double * composition_grid;
        //  Coefficients of 14 moment approximation (vhydro)
        // df ~ ((c0-c2)m^2 + b.c1(u.p) + (4c2-c0)(u.p)^2).Pi + (b.c3 + c4(u.p))p_u.V^u + c5.p_u.p_v.pi^uv
        double *** c0_data;
        double *** c1_data;
        double *** c2_data;
        double *** c3_data;
        double *** c4_data;

        //  Coefficients of Chapman Enskog expansion (vhydro)
        // df ~ ((c0-c2)m^2 + b.c1(u.p) + (4c2-c0)(u.p)^2).Pi + (b.c3 + c4(u.p))p_u.V^u + c5.p_u.p_v.pi^uv
        double *** F_data;
        double *** G_data;
        double *** betabulk_data;
        double *** betaV_data;
        double *** betapi_data;

        // cubic splines of the coefficients as function of temperature only (neglect muB, nB, Vmu)
        // (c1, G = 0) for muB = 0 and (c3, c4, betaV) aren't needed since they couple to baryon diffusion
        // so in the cubic spline evaluation: just set (G, c1, c3, c4) = 0 and betaV = 1 (betaV is in denominator)

        gsl_spline * c0_spline;
        gsl_spline * c2_spline;
        gsl_spline * c3_spline;

        gsl_spline * F_spline;
        gsl_spline * betabulk_spline;
        gsl_spline * betaV_spline;
        gsl_spline * betapi_spline;

        // Jonah coefficients
        const int jonah_points = 301;       // # lambda interpolation points
        const double lambda_min = -1.0;     // lambda min / max values
        const double lambda_max = 2.0;
        const double delta_lambda = (lambda_max - lambda_min) / ((double)jonah_points - 1.0);

        double * lambda_squared_array;      // squared isotropic momentum scale
        double * z_array;                   // renormalization factor (apart from detLambda)
        double * bulkPi_over_Peq_array;     // bulk pressure output
        double bulkPi_over_Peq_max;         // the maximum bulk pressure in the array

        // PCE disables Jonah preparation, so these optional splines may never
        // be allocated. Keep destruction valid for the production df_mode=1.
        gsl_spline * lambda_squared_spline = nullptr; // lambda^2(bulkPi/Peq)
        gsl_spline * z_spline = nullptr;

        Deltaf_Data(ParameterReader * paraRdr_in);
        ~Deltaf_Data();

        void load_df_coefficient_data();    // read the data files in /deltaf_coefficients/vh

        void construct_cubic_splines();

        // I skip the photon because I think it breaks down for lambda = -1
        void compute_jonah_coefficients(particle_info * particle_data, int Nparticle);

        deltaf_coefficients evaluate_df_coefficients(double T, double muB, double E, double P, double bulkPi, double gamma_l, double gamma_s);

        deltaf_coefficients cubic_spline(double T, double E, double P, double bulkPi);

        double calculate_bilinear(double ** f_data, double T, double composition, double TL, double TR, double composition_low, double composition_high, int iTL, int iTR, int i_composition_low, int i_composition_high);

        double calculate_trilinear(double ***f_data, double T, double gamma_l, double gamma_s, double TL, double TR, double gamma_l_low, double gamma_l_high, double gamma_s_low, double gamma_s_high, int iTL, int iTR, int i_light_low, int i_light_high, int i_strange_low, int i_strange_high);
  
        deltaf_coefficients bilinear_interpolation(double T, double composition, double E, double P, double bulkPi);

        deltaf_coefficients trilinear_interpolation(double T, double gamma_l, double gamma_s, double E, double P, double bulkPi);

        void test_df_coefficients(double bulkPi_over_P);

        void compute_particle_densities(particle_info * particle_data, int Nparticle);
};

#endif
