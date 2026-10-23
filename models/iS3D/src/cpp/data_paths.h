#ifndef IS3D_DATA_PATHS_H
#define IS3D_DATA_PATHS_H

#include <fstream>
#include <string>

namespace is3d {

// Data trees prepared before the files were renamed still use the former names
// below; both are read. Former -> canonical names: smash_new -> smash_pce_light,
// smash_pce -> smash_pce_light_strange, smash_eq -> smash_equilibrium,
// pdg_new2.dat -> pdg_smash_pce.dat. The canonical path wins when both exist.
inline std::string data_path(const std::string& root,
                             const std::string& canonical,
                             const std::string& legacy,
                             const std::string& probe = "") {
    const std::string prefix = root + (root.empty() || root.back() == '/' ? "" : "/");
    const std::string preferred = prefix + canonical;
    const std::string previous = prefix + legacy;
    if (std::ifstream(preferred + probe).good()) return preferred;
    if (std::ifstream(previous + probe).good()) return previous;
    return preferred;  // Missing-data errors should identify the canonical path.
}

}  // namespace is3d

#endif
