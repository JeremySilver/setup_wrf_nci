#!/bin/bash

module purge
module load pbs
module load dot
module load nco 
module use /g/data3/hh5/public/modules
module load conda/analysis27
module load wgrib2
## load the *same* as used in the WRF build.env files
## that contains the environment modules used in WRF.
source load_wrf_env.sh
