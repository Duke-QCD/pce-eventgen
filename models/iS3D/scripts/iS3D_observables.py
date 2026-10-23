#!/usr/bin/env python3

import numpy as np
from scipy.integrate import trapz
import os
import sys
from scipy.interpolate import PchipInterpolator

try:
    os.mkdir('observables')
except:
    pass

# Species to summarize: argument 1, else the four-species PCE list in this checkout.
particle_file = (sys.argv[1] if len(sys.argv) > 1 else
                 os.path.join(os.path.dirname(os.path.abspath(__file__)), '../PDG/chosen_particles_PCE.dat'))
particles = np.loadtxt(particle_file)

N = np.zeros(particles.size)
v2 = np.zeros(particles.size)
v3 = np.zeros(particles.size)
v4 = np.zeros(particles.size)
pT = np.zeros(particles.size)

for j,particle in enumerate(particles):
    name = str(int(particle))
    spectra = np.loadtxt('results/dN_pTdpTdphidy_' + name + '.dat',skiprows=1)
    vn = np.loadtxt('results/vn_continuous/vn_' + name + '.dat')
    
    res = np.zeros(24)
    for i in range(24):
        res[i] = trapz(spectra[i*32:(i+1)*32,3]*spectra[i*32:(i+1)*32,2]*2*np.pi,spectra[i*32:(i+1)*32,2])
    N[j] = trapz(res,spectra[::32,1])/(2*np.pi)
    
    spline_y = np.zeros(32)
    for i in range(32):
        spline_y[i] = np.mean(spectra[i::32,3])*2*np.pi*spectra[i,2]
        
    dNpT_spline = PchipInterpolator(spectra[:32,2],spline_y)
    
    v2[j] = trapz(vn[:32,3]*dNpT_spline(vn[:32,1]), vn[:32,1])/trapz(dNpT_spline(vn[:32,1]), vn[:32,1])
    v3[j] = trapz(vn[:32,4]*dNpT_spline(vn[:32,1]), vn[:32,1])/trapz(dNpT_spline(vn[:32,1]), vn[:32,1])
    v4[j] = trapz(vn[:32,5]*dNpT_spline(vn[:32,1]), vn[:32,1])/trapz(dNpT_spline(vn[:32,1]), vn[:32,1])
    
    pT[j] = trapz(dNpT_spline(spectra[:32,2])*spectra[:32,2],spectra[:32,2])/trapz(dNpT_spline(spectra[:32,2]),spectra[:32,2])

np.savetxt('observables/N.dat', N)
np.savetxt('observables/v2.dat', v2)
np.savetxt('observables/v3.dat', v3)
np.savetxt('observables/v4.dat', v4)
np.savetxt('observables/pT.dat', pT)
