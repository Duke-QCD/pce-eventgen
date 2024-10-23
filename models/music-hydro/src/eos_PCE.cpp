// Copyright 2018 @ Chun Shen

#include "eos_PCE.h"
#include "util.h"

#include <sstream>
#include <fstream>
#include <cmath>

using std::stringstream;
using std::string;

EOS_PCE::EOS_PCE(const InitData &DATA_in) : DATA(DATA_in) {
    set_EOS_id(18);
    set_number_of_tables(0);
    set_eps_max(1e5);
    set_flag_muB(false);
    set_flag_muS(false);
    set_flag_muC(false);
}


void EOS_PCE::initialize_eos() {
    // read the lattice EOS pressure, temperature, and 
    music_message.info("reading PCE EOS ...");
    
    auto envPath = get_hydro_env_path();
    stringstream slocalpath;
    slocalpath << "/EOS/PCE";

    string path = slocalpath.str();
    music_message << "from path " << path;
    music_message.flush("info");
   
    set_number_of_tables(10);  // Number of fug_s values. itable here indexes distinct strange fugacities
    resize_table_info_arrays();

    int ntables = get_number_of_tables();

    pressure_tb    = new double** [ntables];
    temperature_tb = new double** [ntables]; 
    cs2_tb         = new double** [ntables];
    energy_tb      = new double** [ntables];
    entropy_tb     = new double** [ntables];
    //std::ifstream pce_eos(path + "/test.dat", std::ios::binary);
    std::ifstream pce_eos(path + "/PCE_eos_smooth_e0p25spacing.dat", std::ios::binary);

    for (int itable = 0; itable < ntables; itable++) {
        std::ifstream* eos_file;
        eos_file = &pce_eos;

	eos_file->clear();
	eos_file->seekg(0);
	// std::cout << itable << std::endl;
        if (!*eos_file) {
            music_message.error("Can not find the EoS file.");
	    exit(1);
        }
	
        e_length[itable]  = 98000;
	
        fugl_length[itable] = 100;
	fugl_bounds[itable] = 0;
	fugl_spacing[itable] = 1/(fugl_length[itable]-1.);
	fugs_length[itable] = ntables;
	fugs_bounds[itable] = 0;
	fugs_spacing[itable] = 1/(fugs_length[itable]-1.);

	nb_length[itable] = fugl_length[itable];
	nb_bounds[itable] = fugl_bounds[itable];
	nb_spacing[itable] = fugl_spacing[itable];
	 
        // allocate memory for pressure arrays
        pressure_tb[itable] = Util::mtx_malloc(fugl_length[itable],
                                               e_length[itable]);
        temperature_tb[itable] = Util::mtx_malloc(fugl_length[itable],
                                                  e_length[itable]);
	cs2_tb[itable] = Util::mtx_malloc(fugl_length[itable], e_length[itable]);
        energy_tb[itable] = Util::mtx_malloc(fugl_length[itable], e_length[itable]);
	entropy_tb[itable] = Util::mtx_malloc(fugl_length[itable], e_length[itable]);

        double temp;
        for (int ii = 0; ii < e_length[itable]; ii++) {
	    for (int j = 0; j < fugl_length[itable]; j++) {
  	        eos_file->read((char*)&temp, sizeof(double));  // e^(1/4) (1/fm)
 	        //temp /= Util::hbarc;      // 1/fm^4
	        if (ii == 0 and j == 0) e_bounds[itable] = temp;
	        if (ii == 1 and j == 0) e_spacing[itable] = temp - e_bounds[itable];
	        if (ii == e_length[itable] - 1 and j == 0 and itable == 0) set_eps_max(std::pow(temp,4));
	        energy_tb[itable][j][ii] = temp;

	        eos_file->read((char*)&temp, sizeof(double));  // P
	        pressure_tb[itable][j][ii] = temp/Util::hbarc;      // 1/fm^4

	        eos_file->read((char*)&temp, sizeof(double));  // s
	        entropy_tb[itable][j][ii] = temp;                   // 1/fm^3

	        eos_file->read((char*)&temp, sizeof(double));  // T
	        temperature_tb[itable][j][ii] = temp/Util::hbarc;   // 1/fm

		eos_file->read((char*)&temp, sizeof(double));  // cs2
		cs2_tb[itable][j][ii] = temp;
	    }
        }

	//	std::cout << temperature_tb[itable][0][0] << " " << temperature_tb[itable][99][99999] << " " << e_bounds[itable] << " " << e_spacing[itable] <<std::endl;
	// if (itable == 1) {
        //     acc = gsl_interp_accel_alloc();
	    
        //     pressure_spline = gsl_spline_alloc(gsl_interp_linear, e_length[itable]);
	//     temperature_spline = gsl_spline_alloc(gsl_interp_linear, e_length[itable]);
	//     cs2_spline = gsl_spline_alloc(gsl_interp_linear, e_length[itable]);
	//     entropy_spline = gsl_spline_alloc(gsl_interp_linear, e_length[itable]);

	//     gsl_spline_init(pressure_spline, energy_tb[itable][0], pressure_tb[itable][0], e_length[itable]);
	//     gsl_spline_init(temperature_spline, energy_tb[itable][0], temperature_tb[itable][0], e_length[itable]);
	//     gsl_spline_init(cs2_spline, energy_tb[itable][0], cs2_tb[itable][0], e_length[itable]);
	//     gsl_spline_init(entropy_spline, energy_tb[itable][0], entropy_tb[itable][0], e_length[itable]);
	// }
    }

//    // Create and open an output file for the interpolated table
//    std::ofstream interpolatedTableFile("interpolated_eos_table.dat", std::ios::binary);

//    // Interpolate and write data to the output file
//    double eMin = 0;  // Minimum energy density for testing
//    double eMax = 0.0001;  // Maximum energy density for testing
//    double eStep = 1e-9; // Energy density step size

//    double tauMin = 0.6;
//    double tauMax = 0.6;
//    double tauStep = 1;

//    for (double e = eMin; e <= eMax; e += eStep) {
//       for (double tau = tauMin; tau <= tauMax; tau += tauStep) {
//            double p = static_cast<double>(get_pressure(e, 0, tau));     // Pressure
//            double s = static_cast<double>(get_entropy(e, 0, tau));     // Entropy
//            double T = static_cast<double>(get_temperature(e, 0, tau)); // Temperature
//            double cs2 = static_cast<double>(get_cs2(e, 0, tau));       // Speed of sound

            // Write the interpolated values to the binary file
//            interpolatedTableFile.write((char*) (&e), sizeof(double));
//            interpolatedTableFile.write((char*) (&p), sizeof(double));
//            interpolatedTableFile.write((char*) (&s), sizeof(double));
//            interpolatedTableFile.write((char*) (&T), sizeof(double));
//            interpolatedTableFile.write((char*) (&cs2), sizeof(double));
//	}
//    }

    // Close the output file
//    interpolatedTableFile.close();
    

    music_message.info("Done reading EOS.");
}


double EOS_PCE::interpolate1D(double e, int table_idx, double ***table) const {
// This is a generic linear interpolation routine for EOS at zero mu_B
// it assumes the class has already read in
//        P(e), T(e), s(e)
// as one-dimensional arrays on an equally spacing lattice grid
// units: e is in 1/fm^4
    //double local_ed = e*hbarc;  // [GeV/fm^3]
    double local_ed = e;

    const double e0       = e_bounds[table_idx];
    const double delta_e  = e_spacing[table_idx];
    const int N_e         = e_length[table_idx];
    const int N_fug       = fugl_length[table_idx];

    // compute the indices
    int idx_e  = static_cast<int>((local_ed - e0)/delta_e);

    // treatment for overflow, use the last two points to do extrapolation
    idx_e  = std::min(N_e - 2, idx_e);

    // check underflow
    idx_e  = std::max(0, idx_e);

    const double frac_e = (local_ed - (idx_e*delta_e + e0))/delta_e;

    double result;
    
    double temp1 = table[table_idx][N_fug-1][idx_e];
    double temp2 = table[table_idx][N_fug-1][idx_e + 1];
    result = temp1*(1. - frac_e) + temp2*frac_e;

    //if (idx_e > 1 and table_idx == 1) {result = gsl_spline_eval(spline, e, acc);}
    return(result);
}


double EOS_PCE::p_e_func(double e, double rhob, double proper_tau) const {
    return(get_dpOverde3(e, rhob, proper_tau));
}

//! This function returns the local temperature in [1/fm]
//! input local energy density eps [1/fm^4] and rhob [1/fm^3]
double EOS_PCE::get_temperature(double e, double rhob, double proper_tau) const {
    double light_fugacity = get_light_fugacity(proper_tau);
    double strange_fugacity = get_strange_fugacity(proper_tau);

    double T;
    if (light_fugacity == 1 and strange_fugacity == 1) T = interpolate1D(std::pow(e,0.25), get_number_of_tables()-1, temperature_tb);
    else T = interpolate3D(std::pow(e,0.25), light_fugacity, strange_fugacity, temperature_tb);
    return(std::max(1e-15, T));
}


//! This function returns the local pressure in [1/fm^4]
//! the input local energy density [1/fm^4], rhob [1/fm^3]
double EOS_PCE::get_pressure(double e, double rhob, double proper_tau) const {
    double light_fugacity = get_light_fugacity(proper_tau);
    double strange_fugacity = get_strange_fugacity(proper_tau);
    double f;
    if (light_fugacity == 1 and strange_fugacity == 1) f = interpolate1D(std::pow(e,0.25), get_number_of_tables()-1, pressure_tb);
    else f = interpolate3D(std::pow(e,0.25), light_fugacity, strange_fugacity, pressure_tb);
    return(std::max(1e-15, f));
}


double EOS_PCE::get_s2e(double s, double rhob, double proper_tau) const {
    double e = get_s2e_finite_rhob(s, 0.0, proper_tau);
    return(e);
}

double EOS_PCE::get_T2e(double T, double rhob, double proper_tau) const {
    double e = get_T2e_finite_rhob(T, 0.0, proper_tau);
    return(e);
}

double EOS_PCE::get_light_fugacity(double proper_tau) const {
    double fugacity;
    double tau0 = DATA.tau0;
    double tau_eq = DATA.tau_eq_l;
    double fug0 = DATA.fug_l_0;

    if (proper_tau < 0) {
        fugacity = 0;
    }

    else if (tau0 <= 0 or tau_eq <= 0) {
        fugacity = 1;
    }

    else {
        fugacity = 1 - (1-fug0)*exp((tau0 - proper_tau)/tau_eq);
	if (fugacity < 0) return(0);
    }

    return(fugacity);
}

double EOS_PCE::get_strange_fugacity(double proper_tau) const {
    double fugacity;
    double tau0 = DATA.tau0;
    double tau_eq = DATA.tau_eq_s;
    double fug0 = DATA.fug_s_0;
  
    if (tau0 <= 0 or tau_eq <= 0) {
        fugacity = 1;
    }
  
    else {
        fugacity = 1 - (1-fug0)*exp((tau0 - proper_tau)/tau_eq);
        if (fugacity < 0) return(0);
    }
  
    return(fugacity);
}

double EOS_PCE::get_cs2(double e, double rhob, double proper_tau) const {
     double light_fugacity = get_light_fugacity(proper_tau);
     double strange_fugacity = get_strange_fugacity(proper_tau);
     double cs2;
     
     if (light_fugacity == 1 and strange_fugacity == 1) cs2 = interpolate1D(std::pow(e,0.25), get_number_of_tables()-1, cs2_tb);
     else cs2 = interpolate3D(std::pow(e,0.25), light_fugacity, strange_fugacity, cs2_tb);

     return(std::max(1e-15,std::min(1./3.-1e-10,cs2)));
     }

double EOS_PCE::get_entropy(double e, double rhob, double proper_tau) const {
     double light_fugacity = get_light_fugacity(proper_tau);
     double strange_fugacity = get_strange_fugacity(proper_tau);

     double entropy;
     if (light_fugacity == 1 and strange_fugacity == 1) entropy = interpolate1D(std::pow(e,0.25), get_number_of_tables()-1, entropy_tb);
     else entropy = interpolate3D(std::pow(e,0.25), light_fugacity, strange_fugacity, entropy_tb);
     return(std::max(1e-15,entropy));
}

double EOS_PCE::get_T_ratio(Cell_small &cell) const {
     double light_fugacity = get_light_fugacity(cell.proper_tau);
     double strange_fugacity = get_strange_fugacity(cell.proper_tau);

     return (get_temperature(cell.epsilon, cell.rhob, cell.proper_tau)/(0.158/Util::hbarc*std::pow(light_fugacity,0.5) + 0.260/Util::hbarc*(1-std::pow(light_fugacity,0.5))));
}
