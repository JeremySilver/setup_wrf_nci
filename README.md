# WRF coordination scripts for NCI (Gadi)

**Updated to run on Gadi: these scripts no longer work on Raijin**

Files included:
* `add_remove_var.txt`: List of variables to add/remove to the standard WRF output stream
* `cleanup_script_template.sh`: Template of per-run clean-up script
* `config.json`: JSON file with configuration variables
* `load_conda_env.sh`: Environment variables required to run `setup_for_wrf.py`
* `load_wrf_env.sh`: Environment variables required to run WRF executables (same as to compile WRF)
* `main_script_template.sh`: Template of the main coordination script script
* `namelist.wps`: Template namelist for WPS
* `namelist.wrf`: Template namelist for WRF
* `nccopy_compress_output.sh`: Script to compress any uncompressed netCDF3 files to deflated netCDF4
* `run_script_template.sh`: Template of the per-run run script
* `setup_for_wrf.py`: Main script to run to prepare the simulations
* `submit_setup.sh`: Script to submit (to the `copyq`) that does the setup

Procedure to run these scripts:
0. [Apply for membership](https://my.nci.org.au/) to the 'sx70' NCI group to use the `WPS_GEOG` dataset. If you also want to use the ERAI analyses, you will need to apply for access to the 'ua8' and 'ub4' NCI groups.
1. Edit the above scripts, particularly `config.json`, `namelist.wrf`, `namelist.wps`, `load_wrf_env.sh` (and possibly also `load_conda_env.sh`).
2. Either submit the setup via `qsub submit_setup.sh` *or* do the following:
..a. Log into one of the `copyq` nodes in interactive mode (via `qsub -I -q copyq -l wd,walltime=2:00:00,ncpus=1,mem=6GB -l storage=scratch/${PROJECT}+gdata/sx70+gdata/hh5`, for example).
..b. Run `./submit_setup.sh` on the command line. Before doing this, replace `${PROJECT}` in the `submit_setup.sh` script, with your `${PROJECT}` shell environment variable - this can be found in your `${HOME}/.config/gadi-login.conf` file.

The python script does the following:
* Reads the `config.json` configuration file
* Performs substitutions of the config file. For example, if used, the shell environment variable `${HOME}` will be replaced by its value when interpreting the script. The variable `wps_dir` is defined within the config file, and if the token `${wps_dir}` appears within the configuration entries, such tokens will be replaced by the value of this variable.
* Configure the main coordination script
* Loop over the WRF jobs, performing the following:
  * Check if the WRF input files for this run are available (`wrfinput_d0?`). If not, perform the following:
    * Check that the geogrid files are available (copies should be found in the directory given by the config variable `nml_dir`). If not available, configure the WPS namelist and run `geogrid.exe` to produce them.
    * Check if the `met_em` files for this run are available. If not, perform the following:
      * Run `link_grib.csh`, configure the WPS namelist and run `ungrib.exe` for the high-resolution SST files (RTG) - this is optional
      * If using the NCEP FNL analysis (available from 2015-07-09), download and subset the grib files
      * Run `link_grib.csh`, configure the WPS namelist and run `ungrib.exe` for the analysis files (ERA Interim or FNL)
      * Run `metgrid.exe` to produce the `met_em` files, moves these to a directory (`METEM`)
    * Link to the `met_em` files (in the `METEM` directory), configure the WRF namelist, run `real.exe`
  * Configure the daily "run" and "cleanup" scripts

To run the WRF model, either submit the main coordination script or the daily run-scripts with `qsub`.


## Notes on the input files and scripts

The following files are configured based on the results of `config.json`: `namelist.wps`, `namelist.wrf`, `cleanup_script_template.sh`, `main_script_template.sh`, `run_script_template.sh`. The tokens to replace are identified with the following format: `${keyword}`. Generally speaking, the values for substitution are defined within the python script (`setup_for_wrf.py`). To change the substitutions, edit the python script in the sections between the lines bounded by `## EDIT:` and `## end edit section`.

The `load_wrf_env.sh` script should contain the *same* environment modules that were used to compile WRF. It is assumed that you have compiled with MPI (i.e. 'distributed memory').

## A few caveats

This has been tested on *Gadi* using the [CLEX CMS WRF setup](https://github.com/coecms/WRF). Users of NCI wanting to run WRF are strongly encouraged to use this version, and to consult [the CMS page on WRF](http://climate-cms.wikis.unsw.edu.au/WRF). These scripts assume that WRF was compiled for MPI multiprocessing (i.e. 'distributed memory' parallelism). 

## Analysis inputs

This script will either use the ERA Interim reanalyses available on NCI or download NCEP FNL 0.25 analyses. This is set in `config.json'. If using the FNL analyses, you need to create an account on the [UCAR CISL](https://rda.ucar.edu) portal, and enter the credentials in `config.json' - this is not terribly secure, so make up a **fresh password** for this site. If switching between the FNL and ERA Interim reanalyses, you will need to change the Vtable file used (set in `config.json'), and also the number of vertical levels (set in `namelist.wrf' file). Also the merger of the RTG SSTs is only done for the ERA Interim analysis, and this step is optional (set in `config.json').

## Notes on the structure of the output

All the main WRF output will be produced within subfolders of the directory given by variable `run_dir` in the config file. It will have the following substructure:
* `${run_dir}/METEM`: contains any `met_em` files produced (once the `wrfinput_d0?` files have been created, these `met_em` files can be deleted).
* `${run_dir}/${YYYYMMDDHH}`: One folder per WRF run, where `${YYYYMMDDHH}` is the (UTC) date-stamp of the starting time of the corresponding WRF run (n.b. this run may actually start earlier if spinup is requested.
One exception to this is that the GEOGRID output files (`geo_em.d0?.nc`) are moved to the directory given by the variable `nml_dir` in the config file.

## General principles

The scripts have been developed with the following principles:
* Data should not be defined in multiple locations (or at least as few locations as possible)
* Symbolic links are used where possible in preference to new copies of data files, scripts and executables. When configuration is required, new copies are always generated.
* Inputs and outputs should be checked where possible and informative error messages should be given. Checks are fairly minimal (generally just that paths exist, not whether the contents are appropriate).
* The data footprint should be kept to a minimum.
* Progress is reported as the script progresses.

## General disclaimer

This software is provided "as is". I maintain it in the little spare time I have. I have limited time to debug things when they break, so please be prepared to solve problems yourself.
