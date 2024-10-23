// Copyright 2018 @ Chun Shen

#ifndef SRC_EOS_H_
#define SRC_EOS_H_

#include "eos_base.h"
#include "data.h"
#include <memory>
#include "grid.h"

//! This is a wrapper class for the equation of state
class EOS {
 private:
    const int eos_id;
    const InitData &DATA;

    std::unique_ptr<EOS_base> eos_ptr;

 public:
    EOS() = default;
    EOS(const InitData &DATA_in);

    ~EOS() {};

    // functions to call the function pointers
    double get_pressure   (double e, double rhob, double proper_tau) const {return(eos_ptr->get_pressure(e, rhob, proper_tau));}
    double get_temperature(double e, double rhob, double proper_tau) const {return(eos_ptr->get_temperature(e, rhob, proper_tau));}
    double get_entropy    (double e, double rhob, double proper_tau) const {return(eos_ptr->get_entropy(e, rhob, proper_tau));}
    double get_cs2        (double e, double rhob, double proper_tau) const {return(eos_ptr->get_cs2(e, rhob, proper_tau));}
    double get_dpde       (double e, double rhob, double proper_tau) const {return(eos_ptr->p_e_func(e, rhob, proper_tau));}
    double get_dpdrhob    (double e, double rhob, double proper_tau) const {return(eos_ptr->p_rho_func(e, rhob, proper_tau));}
    double get_muB        (double e, double rhob, double proper_tau) const {return(eos_ptr->get_muB(e, rhob, proper_tau));}
    double get_muS        (double e, double rhob, double proper_tau) const {return(eos_ptr->get_muS(e, rhob, proper_tau));}
    double get_muC        (double e, double rhob, double proper_tau) const {return(eos_ptr->get_muC(e, rhob, proper_tau));}
    double get_s2e        (double s, double rhob, double proper_tau) const {return(eos_ptr->get_s2e(s, rhob, proper_tau));}
    double get_T2e        (double T, double rhob, double proper_tau) const {return(eos_ptr->get_T2e(T, rhob, proper_tau));}
    double get_light_fugacity  (double proper_tau) const {return(eos_ptr->get_light_fugacity(proper_tau));}
    double get_strange_fugacity (double proper_tau) const {return(eos_ptr->get_strange_fugacity(proper_tau));}

    double get_eps_max() const {return(eos_ptr->get_eps_max());}
    void   check_eos()   const {return(eos_ptr->check_eos());}
    double get_T_ratio    (Cell_small &cell) const {return(eos_ptr->get_T_ratio(cell));}
};

#endif  // SRC_EOS_H_
