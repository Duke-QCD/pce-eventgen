// Copyright 2018 @ Chun Shen

#ifndef SRC_EOS_PCE_H_
#define SRC_EOS_PCE_H_

#include "eos_base.h"
#include "data.h"
#include "grid.h"

// WARNING(PCE): EOS 18 assumes zero conserved-charge chemical potentials;
// evolving a baryon current does not supply finite-density thermodynamics.
class EOS_PCE : public EOS_base {
 private:
    const InitData &DATA;
   
 public:
    EOS_PCE(const InitData &DATA_in);
    
    void initialize_eos();
    double interpolate1D(double e, int table_idx, double ***table) const;
    double smoothstep(double x, double x0, double x1) const;
    double smooth(double e, double fug_l, double fug_s) const;
    double p_e_func       (double e, double rhob, double proper_tau) const;
    double get_dpOverde3(double e, double rhob, double proper_tau) const;
    double get_temperature(double e, double rhob, double proper_tau) const;
    double get_pressure   (double e, double rhob, double proper_tau) const;
    double get_s2e        (double s, double rhob, double proper_tau) const;
    double get_T2e        (double T, double rhob, double proper_tau) const;
    double get_light_fugacity  (double proper_tau) const;
    double get_strange_fugacity (double proper_tau) const;
    double get_cs2        (double e, double rhob, double proper_tau) const;
    double get_entropy   (double e, double rhob, double proper_tau) const;
    double get_T_ratio    (Cell_small &cell) const;

    void check_eos() const {check_eos_no_muB();}
};

#endif  // SRC_EOS_PCE_H_
