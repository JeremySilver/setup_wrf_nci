#!/bin/bash
#PBS -N setupWRF
#PBS -l walltime=10:00:00
#PBS -l mem=4GB
#PBS -l ncpus=1
#PBS -j oe
#PBS -q copyq
#PBS -l wd
#PBS -l storage=scratch/${PROJECT}+gdata/sx70+gdata/hh5+gdata/ua8+gdata/ub4

source load_conda_env.sh

ulimit -s unlimited

python setup_for_wrf.py -c config.json 

