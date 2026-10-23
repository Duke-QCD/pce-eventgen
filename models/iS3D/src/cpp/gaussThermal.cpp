
#include <stdlib.h>
#include <cmath>
#include "gaussThermal.h"
#include <vector>
#include <set>

// gauss integration routine
double GaussThermal(double thermal_integrand(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity), double * pbar_root, double * pbar_weight, int pbar_pts, double mbar, double alphaB, double baryon, double sign, double gamma_l, double gamma_s, int mcid)
{
	double integral = 0.0;
	double fugacity = hadron_fugacity(gamma_l, gamma_s, mcid);
	for(int k = 0; k < pbar_pts; k++)
	{
	  integral += pbar_weight[k] * thermal_integrand(pbar_root[k], mbar, alphaB, baryon, sign, fugacity);
	}
	return integral;
}

// Custom PCE composition shared by thermal integrals and the main sampler:
// count light/strange valence digits, apply the retained eta mixing assignments,
// and map each quark occupancy through 0.15 + 0.85*gamma. Preserve these same
// conventions when generating EOS and delta-f tables.
double hadron_fugacity(double gamma_l, double gamma_s, int mcid)
{
  // Extract quark content digits (1 = d, 2 = u, 3 = s)
  int q1 = (std::abs(mcid) / static_cast<int>(std::pow(10, 1))) % 10;
  int q2 = (std::abs(mcid) / static_cast<int>(std::pow(10, 2))) % 10;
  int q3 = (std::abs(mcid) / static_cast<int>(std::pow(10, 3))) % 10;

  std::vector<int> quarks = {q1, q2, q3};

  // Default light and strange quark counts
  double light = 0.0;
  double strange = 0.0;

  for (int q : quarks) {
    if (q == 1 || q == 2) light += 1.0;
    if (q == 3) strange += 1.0;
  }

  // Special cases: η and η′
  std::set<int> eta_like = {221, 100221, 9020221, 10225};
  std::set<int> eta_prime_like = {331, 100331};

  if (eta_like.count(mcid)) {
    light = 2.0 / 3.0;
    strange = 4.0 / 3.0;
  } else if (eta_prime_like.count(mcid)) {
    light = 4.0 / 3.0;
    strange = 2.0 / 3.0;
  }

  // Effective hadron fugacity after the custom occupancy mapping.
  return std::pow(0.15 + 0.85*gamma_l, light) * std::pow(0.15 + 0.85*gamma_s, strange);
}

// equilibrium particle density
double neq_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity)
{
	double Ebar = sqrt(pbar*pbar + mbar*mbar);

	// gauss laguerre (a = 1)
	return pbar * exp(pbar) / (exp(Ebar - baryon*alphaB)/fugacity + sign);
}

// // equilibrium energy density
// double E_int(double pbar, double mbar, double alphaB, double baryon, double sign)
// {
// 	double Ebar = sqrt(pbar * pbar + mbar * mbar);
// 	// gauss laguerre (a = 2)
// 	return Ebar * exp(pbar) / (exp(Ebar - baryon * alphaB) + sign);
// }

// // equilibrium pressure
// double P_int(double pbar, double mbar, double alphaB, double baryon, double sign)
// {
// 	double Ebar = sqrt(pbar * pbar + mbar * mbar);
// 	// gauss laguerre (a = 2)
// 	return pbar * pbar / Ebar * exp(pbar) / (exp(Ebar - baryon * alphaB) + sign);
// }


// for linearized particle density corrections
double J10_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity)
{
	double Ebar = sqrt(pbar*pbar + mbar*mbar);
	double qstat = exp(Ebar - baryon*alphaB)/fugacity + sign;

	// gauss laguerre (a = 1)
        return pbar * exp(pbar + Ebar - baryon*alphaB)/fugacity / (qstat*qstat);
}

double J11_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity)
{
	double Ebar = sqrt(pbar * pbar + mbar * mbar);
	double qstat = exp(Ebar - baryon * alphaB)/fugacity + sign;
	// gauss laguerre (a = 1)
	return pbar * pbar * pbar / (Ebar * Ebar) * exp(pbar + Ebar - baryon * alphaB)/fugacity / (qstat * qstat);
}

double J20_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity)
{
	double Ebar = sqrt(pbar*pbar + mbar*mbar);
	double qstat = exp(Ebar - baryon*alphaB)/fugacity + sign;

	// gauss laguerre (a = 2)
	return Ebar * exp(pbar + Ebar - baryon*alphaB)/fugacity / (qstat*qstat);
}

double J30_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity)
{
	double Ebar = sqrt(pbar * pbar + mbar * mbar);
	double qstat = exp(Ebar - baryon * alphaB)/fugacity + sign;
	// gauss laguerre (a = 3)
        return Ebar * Ebar / pbar * exp(pbar + Ebar - baryon * alphaB)/fugacity / (qstat * qstat);
}

double J31_int(double pbar, double mbar, double alphaB, double baryon, double sign, double fugacity)
{
	double Ebar = sqrt(pbar * pbar + mbar * mbar);
	double qstat = exp(Ebar - baryon * alphaB)/fugacity + sign;
	// gauss laguerre (a = 3)
	return pbar * exp(pbar + Ebar - baryon * alphaB)/fugacity / (qstat * qstat);
}





// for Jonah's coefficient calculation

double Gauss1D_mod(double modified_1D_integrand(double pbar, double mbar, double lambda, double sign, double fugacity), double * pbar_root, double * pbar_weight, int pbar_pts, double mbar, double lambda, double sign, double gamma_l, double gamma_s, int mcid)
{
	double sum = 0.0;
	double fugacity = hadron_fugacity(gamma_l, gamma_s, mcid);
	for(int k = 0; k < pbar_pts; k++) sum += pbar_weight[k] * modified_1D_integrand(pbar_root[k], mbar, lambda, sign, fugacity);
	return sum;
}

double E_mod_int(double pbar, double mbar, double lambda, double sign, double fugacity)
{
	double scale2 = (1.0 + lambda) * (1.0 + lambda);

	double Ebar = sqrt(pbar*pbar + mbar*mbar);

	return sqrt(pbar*pbar*scale2 + mbar*mbar) * exp(pbar) / (exp(Ebar)/fugacity + sign);
}

double P_mod_int(double pbar, double mbar, double lambda, double sign, double fugacity)
{
	double scale2 = (1.0 + lambda) * (1.0 + lambda);

	double Ebar = sqrt(pbar*pbar + mbar*mbar);

	return pbar*pbar*scale2 / sqrt(pbar*pbar*scale2 + mbar*mbar) * exp(pbar) / (exp(Ebar)/fugacity + sign);
}














