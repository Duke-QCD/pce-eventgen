// Copyright 2018 @ Chun Shen
// PCE: EOS 18, tabulated in (e^(1/4), gamma_l, gamma_s) with time-dependent
// light and strange fugacities.
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

    // One independent file per strange-fugacity slice: 0.dat ... 29.dat.
    // Each file is [q index][gamma_l index][q, P, s, T, cs2], five doubles;
    // q=epsilon^(1/4), with 10000 energy points and 30 light occupancies.
    music_message.info("reading PCE EOS ...");

    std::string eos_table_dir;

    const char* eos_env = std::getenv("HIC_EOS_PCE_DIR");

    if (eos_env != nullptr && std::string(eos_env).size() > 0) {
        eos_table_dir = std::string(eos_env);
    } else {
        std::cerr << "ERROR: HIC_EOS_PCE_DIR is not set. "
                  << "Set it to the PCE EoS table directory." << std::endl;
        std::exit(1);
    }

    if (!eos_table_dir.empty() && eos_table_dir.back() != '/') {
        eos_table_dir += "/";
    }

    std::stringstream slocalpath;
    slocalpath << eos_table_dir;

    string path = slocalpath.str();
    music_message << "from path " << path;
    music_message.flush("info");

    set_number_of_tables(30);
    resize_table_info_arrays();

    int ntables = get_number_of_tables();

    pressure_tb    = new double** [ntables];
    temperature_tb = new double** [ntables];
    cs2_tb         = new double** [ntables];
    energy_tb      = new double** [ntables];
    entropy_tb     = new double** [ntables];

    for (int itable = 0; itable < ntables; itable++) {
        std::ifstream pce_eos(path + std::to_string(itable) + ".dat", std::ios::binary);
        std::ifstream* eos_file;
        eos_file = &pce_eos;

	eos_file->clear();
	eos_file->seekg(0);

        if (!*eos_file) {
            music_message.error("Can not find the EoS file.");
	    exit(1);
        }

        e_length[itable]  = 10000;

        fugl_length[itable] = 30;
	fugl_bounds[itable] = 0;
	fugl_spacing[itable] = 1/(fugl_length[itable]-1.);
	fugs_length[itable] = 30;
	fugs_bounds[itable] = 0;
	fugs_spacing[itable] = 1/(fugs_length[itable]-1.);

	nb_length[itable] = fugl_length[itable];
	nb_bounds[itable] = fugl_bounds[itable];
	nb_spacing[itable] = fugl_spacing[itable];

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
	        eos_file->read((char*)&temp, sizeof(double));  // e^(1/4) [1/fm]
	        if (ii == 0 and j == 0) e_bounds[itable] = temp;
	        if (ii == 1 and j == 0) e_spacing[itable] = temp - e_bounds[itable];
	        if (ii == e_length[itable] - 1 and j == 0 and itable == 0) set_eps_max(std::pow(temp,4));
	        energy_tb[itable][j][ii] = temp;

	        eos_file->read((char*)&temp, sizeof(double));  // P [GeV/fm^3]
	        pressure_tb[itable][j][ii] = temp/Util::hbarc;  // [1/fm^4]

	        eos_file->read((char*)&temp, sizeof(double));  // s [1/fm^3]
	        entropy_tb[itable][j][ii] = temp;

	        eos_file->read((char*)&temp, sizeof(double));  // T [GeV]
	        temperature_tb[itable][j][ii] = temp/Util::hbarc;  // [1/fm]

		eos_file->read((char*)&temp, sizeof(double));  // c_s^2
		cs2_tb[itable][j][ii] = temp;
	    }
        }

    }

    music_message.info("Done reading EOS.");
}

// Linear interpolation in e^(1/4) [1/fm] along the equilibrium (gamma = 1) row.
double EOS_PCE::interpolate1D(double e, int table_idx, double ***table) const {
    double local_ed = e;

    const double e0       = e_bounds[table_idx];
    const double delta_e  = e_spacing[table_idx];
    const int N_e         = e_length[table_idx];
    const int N_fug       = fugl_length[table_idx];

    int idx_e  = static_cast<int>((local_ed - e0)/delta_e);

    idx_e  = std::min(N_e - 2, idx_e);

    idx_e  = std::max(0, idx_e);

    const double frac_e = (local_ed - (idx_e*delta_e + e0))/delta_e;

    double result;

    double temp1 = table[table_idx][N_fug-1][idx_e];
    double temp2 = table[table_idx][N_fug-1][idx_e + 1];
    result = temp1*(1. - frac_e) + temp2*frac_e;

    return(result);
}

// Low-density smoothing: below epsilon = 1e-6 [1/fm^4], get_temperature
// multiplies T by smooth switches in epsilon (1e-8 to 1e-6 [1/fm^4]) and in each
// fugacity (1e-4 to 1), so dilute cells go smoothly to zero temperature.
double EOS_PCE::smoothstep(double x, double x0, double x1) const {
    if (x <= x0) return 0.0;
    if (x >= x1) return 1.0;
    double t = (x - x0)/(x1 - x0);

    return t * t * t * (t * (6.0 * t - 15.0) + 10.0);
}

double EOS_PCE::smooth(double e, double fug_l, double fug_s) const {
    return smoothstep(e,1e-8,1e-6)*smoothstep(fug_l,1e-4,1.0)*smoothstep(fug_s,1e-4,1.0);
}

double EOS_PCE::p_e_func(double e, double rhob, double proper_tau) const {
    return(get_dpOverde3(e, rhob, proper_tau));
}

double EOS_PCE::get_dpOverde3(double e, double rhob, double proper_tau) const {
    return(get_cs2(e, rhob, proper_tau));
}

//! Local temperature [1/fm] from energy density e [1/fm^4] at the cell clock.
double EOS_PCE::get_temperature(double e, double rhob, double proper_tau) const {

    double light_fugacity = get_light_fugacity(proper_tau);
    double strange_fugacity = get_strange_fugacity(proper_tau);

    double T;
    if (light_fugacity == 1 and strange_fugacity == 1) T = interpolate1D(std::pow(e,0.25), get_number_of_tables()-1, temperature_tb);
    else T = interpolate3D(std::pow(e,0.25), light_fugacity, strange_fugacity, temperature_tb);
    if (e < 1e-6) T *= smooth(e,light_fugacity,strange_fugacity);

    return(std::max(1e-15, T));
}

//! Local pressure [1/fm^4] from energy density e [1/fm^4] at the cell clock.
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

// The local advected clock controls independent exponential relaxation of the
// two occupancies. A negative clock is reported as an error.
double EOS_PCE::get_light_fugacity(double proper_tau) const {
    double fugacity;
    double tau0 = DATA.tau0;
    double tau_eq = DATA.tau_eq_l;
    double fug0 = DATA.fug_l_0;

    if (proper_tau < 0) {
        std::cout << "ERROR: Invalid proper tau: " << proper_tau << std::endl;
        fugacity = 0;
    }

    else if (tau0 < 0 or tau_eq <= 0) {
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

    if (proper_tau < 0) {
        std::cout << "ERROR: Invalid proper tau: " << proper_tau << std::endl;
        fugacity = 0;
    }

    else if (tau0 < 0 or tau_eq <= 0) {
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

// PCE switching temperature [GeV]: 0.260 - 0.090*sqrt(gamma_l)
// - 0.012*sqrt(gamma_s), approaching 0.158 in chemical equilibrium.
double EOS_PCE::get_T_ratio(Cell_small &cell) const {
     double light_fugacity = get_light_fugacity(cell.proper_tau);
     double strange_fugacity = get_strange_fugacity(cell.proper_tau);

     return (get_temperature(cell.epsilon, cell.rhob, cell.proper_tau)/(0.158/Util::hbarc*std::pow(strange_fugacity,0.5) + 0.170/Util::hbarc*(std::pow(light_fugacity,0.5)-std::pow(strange_fugacity,0.5)) + 0.260/Util::hbarc*(1-std::pow(light_fugacity,0.5))));
}
