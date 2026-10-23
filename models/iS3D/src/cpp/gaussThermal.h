
#ifndef GAUSSTHERMAL_H
#define GAUSSTHERMAL_H

double GaussThermal(double thermal_integrand(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity), double * pbar_root, double * pbar_weight, int pbar_pts, double mbar, double alphaB, double baryon, double sign, double gamma_l, double gamma_s, int mcid);

// calculate fugacity for a given hadron
double hadron_fugacity(double gamma_l, double gamma_s, int mcid);

// equilibrium particle density
double neq_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity);


// for linearized particle density
double J10_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity);
double J11_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity);
double J20_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity);
double J30_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity);
double J31_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity);


// for jonah coefficient calculation
double Gauss1D_mod(double modified_1D_integrand(double pbar, double mbar, double lambda, double sign, double fugacity), double * pbar_root, double * pbar_weight, int pbar_pts, double mbar, double lambda, double sign, double gamma_l, double gamma_s, int mcid);

double E_mod_int(double pbar, double mbar, double lambda, double sign, double fugacity);
double P_mod_int(double pbar, double mbar, double lambda, double sign, double fugacity);


#endif
