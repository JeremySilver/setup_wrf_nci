import json
import datetime
import pytz
import io
import re
import os
import errno    
import argparse
import math
import f90nml
import shutil
import subprocess
import glob
import sys
import pdb
import time
import resource
import copy
import stat
import netCDF4
from downloadFNLanalyses import downloadFNL

## get command line arguments
parser = argparse.ArgumentParser()

parser.add_argument("-c", "--configFile", help="Path to configuration file", default = 'config.json')
args = parser.parse_args()
configFile = args.configFile

## read the config file
assert os.path.exists(configFile), "No configuration file was found at {}".format(configFile)

try:
    f = open(configFile,'rt')
    input_str = f.read()
    f.close()
except Exception,e:
    print "Problem reading in configuration file"
    print str(e)
    sys.exit()

    
## parse the config file
try:
    ## strip out the comments
    input_str = re.sub(r'#.*\n', '\n', input_str)
    config = json.loads(input_str)
except Exception,e:
    print "Problem parsing in configuration file"
    print str(e)
    sys.exit()

## add some environment variables to the config that may be needed for substitutions
envVarsToInclude = config["environment_variables_for_substitutions"].split(',')
for envVarToInclude in envVarsToInclude:
    if envVarToInclude in os.environ.keys():
        config[envVarToInclude] = os.environ[envVarToInclude]

## do the substitutions
avail_keys = config.keys()
iterationCount = 0
while iterationCount < 10:
    ## check if any entries in the config dictionary need populating
    foundToken = False
    for key, value in config.iteritems():
        if isinstance(value, basestring):
            if (value.find('${') >= 0):
                foundToken = True
    ##
    if foundToken:
        for avail_key in avail_keys:
            key = '${%s}' % avail_key
            value = config[avail_key]
            for k in avail_keys:
                if isinstance(config[k], basestring):
                    if config[k].find(key) >= 0:
                        config[k] = config[k].replace(key,value)
    else:
        break
    ##
    iterationCount += 1

## parameters that should agree for the WRF and WPS namelists
namelistParamsThatShouldAgree = [
    {'wrf_var': 'max_dom','wrf_group': 'domains', 'wps_var': 'max_dom','wps_group': 'share'},
    {'wrf_var': 'interval_seconds','wrf_group': 'time_control', 'wps_var': 'interval_seconds','wps_group': 'share'},
    {'wrf_var': 'parent_id','wrf_group': 'domains', 'wps_var': 'parent_id','wps_group': 'geogrid'}, 
    {'wrf_var': 'parent_grid_ratio','wrf_group': 'domains', 'wps_var': 'parent_grid_ratio','wps_group': 'geogrid'}, 
    {'wrf_var': 'i_parent_start','wrf_group': 'domains', 'wps_var': 'i_parent_start','wps_group': 'geogrid'}, 
    {'wrf_var': 'j_parent_start','wrf_group': 'domains', 'wps_var': 'j_parent_start','wps_group': 'geogrid'}, 
    {'wrf_var': 'e_we','wrf_group': 'domains', 'wps_var': 'e_we','wps_group': 'geogrid'}, 
    {'wrf_var': 'e_sn','wrf_group': 'domains', 'wps_var': 'e_sn','wps_group': 'geogrid'}, 
    {'wrf_var': 'dx','wrf_group': 'domains', 'wps_var': 'dx','wps_group': 'geogrid'}, 
    {'wrf_var': 'dy','wrf_group': 'domains', 'wps_var': 'dy','wps_group': 'geogrid'}]

assert iterationCount < 10, "Config key substitution exceeded iteration limit..."

## check that requisite keys are present
requisite_keys = ["run_name","start_date", "end_date"]
for requisite_key in requisite_keys:
    assert requisite_key in avail_keys, "Key {} was not in the available configuration keys".format(requisite_key)

## parse boolean keys
truevals = ['true', '1', 't', 'y', 'yes']
falsevals = ['false', '0', 'f', 'n', 'no']
boolvals = truevals + falsevals
bool_keys = ["run_as_one_job", "submit_wrf_now", "submit_wps_component", "only_edit_namelists","restart",'delete_metem_files',"use_high_res_sst_data", 'regional_subset_of_grib_data']
for bool_key in bool_keys:
    assert config[bool_key].lower() in boolvals,'Key {} not a recognised boolean value'.format(bool_key)
    config[bool_key] = (config[bool_key].lower() in truevals)

assert config['analysis_source'] in ['ERAI', 'FNL'], 'Key analysis_source must be one of ERAI or FNL'

# execfile("/opt/Modules/default/init/python")

## make the stack size unlimited (the equivalent of `ulimit -s unlimited` in bash)
resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

scripts = {}
dailyScriptNames = ['run','cleanup']
scriptNames = ['main','run','cleanup']
for scriptName in scriptNames:
    templateScript = '{}_script_template'.format(scriptName)
    ## read the template run script
    assert os.path.exists(config[templateScript]), "No template {} script was found at {}".format(scriptName, config[templateScript])
    try:
        f = open(config[templateScript],'rt')
        scripts[scriptName] = f.readlines()
        f.close()
    except Exception,e:
        print "Problem reading in template {} script".format(scriptName)
        print str(e)

## make directories recursively, and safely
## this function is a copy of: https://stackoverflow.com/a/600612/356426
def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:  # Python >2.5
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else:
            raise

## function to parse times
def process_date_string(datestring):
    datestring = datestring.strip().rstrip()
    ## get the timezone
    if len(datestring) <= 19:
        tz = pytz.UTC
    else:
        tzstr = datestring[20:]
        tz = pytz.timezone(tzstr)
    ##
    date = datetime.datetime.strptime(datestring,'%Y-%m-%d %H:%M:%S %Z')
    date = tz.localize(date)
    ##
    return date

def purge(dir, pattern):
    for f in os.listdir(dir):
        if re.search(pattern, f) > 0:
            os.remove(os.path.join(dir, f))

def move_pattern_to_dir(sourceDir, pattern, destDir):
    for f in os.listdir(sourceDir):
        if re.search(pattern, f) > 0:
            os.rename(os.path.join(sourceDir, f), os.path.join(destDir, f))

def link_pattern_to_dir(sourceDir, pattern, destDir):
    for f in os.listdir(sourceDir):
        ## pdb.set_trace()
        if re.search(pattern, f) > 0:
            src = os.path.join(sourceDir, f)
            dst = os.path.join(destDir, f)
            if not os.path.exists(dst): os.symlink(src, dst)


def grep_file(regex, inFile):
    fl = open(inFile, "r")
    lines = fl.readlines()
    fl.close()
    out = [ line for line in lines if line.find(regex) >= 0 ]
    return out

def grep_lines(regex, lines):
    if type(lines) == type(''):
        lines = lines.split('\n')
    out = [ line for line in lines if line.find(regex) >= 0 ]
    return out


def compressNCfile(filename,ppc = None):
    '''Compress a netCDF3 file to netCDF4 using ncks
    
    Args: 
        filename: Path to the netCDF3 file to commpress
        ppc: number of significant digits to retain (default is to retain all)

    Returns:
        Nothing
    '''
    
    if os.path.exists(filename):
        print "Compress file {} with ncks".format(filename)
        command = 'ncks -4 -L4 -O {} {}'.format(filename, filename)
        print '\t'+command
        commandList = command.split(' ')
        if ppc is None:
            ppcText = ''
        else:
            if not isinstance(ppc, int):
                raise RuntimeError("Argument ppc should be an integer...")
            elif ppc < 1 or ppc > 6:
                raise RuntimeError("Argument ppc should be between 1 and 6...")
            else:
                ppcText = '--ppc default={}'.format(ppc)
                commandList = [commandList[0]] + ppcText.split(' ') + commandList[1:]
        ##
        ##
        p = subprocess.Popen(commandList, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        if len(stderr) > 0 or len(stdout) > 0:
            print "stdout = " + stdout
            print "stderr = " + stderr
            raise RuntimeError("Error from ncks...")
    else:
        print "File {} not found...".format(filename)



## parse the times
try:
    start_date = process_date_string(config['start_date'])
    end_date   = process_date_string(config['end_date'])
except Exception,e:
    print "Problem parsing start/end times"
    print str(e)

## check that the dates are in the right order
assert end_date > start_date, "End date should be after start date"

## calculate the number of jobs
run_length_hours = (end_date - start_date).total_seconds()/3600.
number_of_jobs = int(math.ceil(run_length_hours/float(config["num_hours_per_run"])))

## check that namelist template files are present
WPSnmlPath = config["namelist_wps"]
WRFnmlPath = config["namelist_wrf"]
assert os.path.exists(WPSnmlPath),"File WPS namelist not found at {}".format(WPSnmlPath)
assert os.path.exists(WRFnmlPath),"File WRF namelist not found at {}".format(WRFnmlPath)

## read the WPS
WPSnml = f90nml.read(WPSnmlPath)
WRFnml = f90nml.read(WRFnmlPath)

## check that the parameters do agree between the WRF and WPS namelists
print '\t\tCheck for consistency between key parameters of the WRF and WPS namelists'
for paramDict in namelistParamsThatShouldAgree:
    WRFval = WRFnml[paramDict['wrf_group']][paramDict['wrf_var']]
    WPSval = WPSnml[paramDict['wps_group']][paramDict['wps_var']]
    ## the dx,dy variables need special treatment - they are handled differently in the two namelists
    if paramDict['wrf_var'] in ['dx','dy']:
        if WPSnml['share']['max_dom'] == 1:
            if type(WRFval) == type([]):
                assert WRFval[0] == WPSval, "Mismatched values for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])
            else:
                assert WRFval == WPSval, "Mismatched values for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])
        else:
            expectedVal = [float(WPSnml['geogrid'][paramDict['wps_var']])]
            for idom in range(1,WPSnml['share']['max_dom']):
                try:
                    expectedVal.append(expectedVal[-1]/float(WPSnml['geogrid']['parent_grid_ratio'][idom]))
                except:
                    pdb.set_trace()
            ##
            assert len(WRFval) == len(expectedVal), "Mismatched length for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])
            assert all([a == b for a,b in zip(WRFval,expectedVal)]), "Mismatched values for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])
    else:
        assert type(WRFval) == type(WPSval), "Mismatched type for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])
        if type(WRFval) == type([]):
            assert len(WRFval) == len(WPSval), "Mismatched length for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])
            assert all([a == b for a,b in zip(WRFval,WPSval)]), "Mismatched values for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])
        else:
            assert WRFval == WPSval, "Mismatched values for variable {} between the WRF and WPS namelists".format(paramDict['wrf_var'])

## get the number of domains
nDom = WPSnml['share']['max_dom']

## get the total run length
run_length_total_hours = config["num_hours_per_run"] + config["num_hours_spin_up"]

## check that the output directory exists - if not, create it
if not os.path.exists(config["run_dir"]):
    mkdir_p(config["run_dir"])

print '\t\tGenerate the main coordination script'

## write out the main coordination script

############## EDIT: the following are the substitutions used for the main run script
substitutions = {'STARTDATE'   : start_date.strftime('%Y%m%d%H'),
                 'njobs'       : '{}'.format(number_of_jobs),
                 'nhours'      : '{}'.format(config['num_hours_per_run']),
                 'RUNNAME'     : config['run_name'],
                 'NUDGING'     : '{}'.format(not config['restart']).lower(),
                 'runAsOneJob' : '{}'.format(config['run_as_one_job']).lower(),
                 'RUN_DIR'     : config['run_dir'] }
############## end edit section #####################################################

## do the substitutions
thisScript = copy.copy(scripts['main'])
for avail_key in substitutions.keys():
    key = '${%s}' % avail_key
    value = substitutions[avail_key]
    thisScript = [ l.replace(key,value) for l in thisScript ]
## write out the lines
scriptFile = '{}.sh'.format('main')
scriptPath = os.path.join(config['run_dir'], scriptFile)
f = file(scriptPath,'w')
f.writelines(thisScript)
f.close()
## make executable
os.chmod(scriptPath,os.stat(scriptPath).st_mode | stat.S_IEXEC) 


## loop through the different days
for ind_job in range(number_of_jobs):
    job_start = start_date + datetime.timedelta(seconds = 3600 * ind_job * int(config["num_hours_per_run"])) - datetime.timedelta(seconds = 3600 * int(config["num_hours_spin_up"]))
    job_start_usable = start_date + datetime.timedelta(seconds = 3600 * ind_job * int(config["num_hours_per_run"]))
    job_end   = start_date + datetime.timedelta(seconds = 3600 * (ind_job+1) * int(config["num_hours_per_run"]))
    print "Start preparation for the run beginning {}".format(job_start_usable.date())
    ##
    yyyymmddhh_start = job_start_usable.strftime('%Y%m%d%H')
    run_dir_with_date = os.path.join(config["run_dir"],yyyymmddhh_start)
    if not os.path.exists(run_dir_with_date):
        mkdir_p(run_dir_with_date)
    ##
    os.chdir(run_dir_with_date)
    ## check that the WRF initialisation files exist
    print "\tCheck that the WRF initialisation files exist"
    wrfbdyPath = os.path.join(run_dir_with_date,'wrfbdy_d01') ## check for the BCs
    wrfInitFilesExist = os.path.exists(wrfbdyPath)
    for iDom in range(nDom):
        dom = 'd0{}'.format(iDom+1)
        wrfinputPath = os.path.join(run_dir_with_date,'wrfinput_{}'.format(dom)) ## check for the ICs
        wrfInitFilesExist = wrfInitFilesExist and os.path.exists(wrfinputPath)
        wrflowinpPath = os.path.join(run_dir_with_date,'wrflowinp_{}'.format(dom)) ## check for SSTs
        wrfInitFilesExist = wrfInitFilesExist and os.path.exists(wrflowinpPath)
    ##
    if not config['only_edit_namelists']:
        if not wrfInitFilesExist:
            print "\t\tThe WRF initialisation files did not exist..."
            # Check that the topography files exist
            geoFilesExist = True
            print "\tCheck that the geo_em files exist"
            for iDom in range(nDom):
                dom = 'd0{}'.format(iDom+1)
                geoFile = 'geo_em.{}.nc'.format(dom)
                geoPath = os.path.join(config["nml_dir"],geoFile)
                if not os.path.exists(geoPath):
                    geoFilesExist = False
            ## If not, produce them
            if geoFilesExist:
                print "\t\tThe geo_em files were indeed found"
            else:
                print "\t\tThe geo_em files did not exist - create them"
                ## copy the WPS namelist
                src = WPSnmlPath
                dst = os.path.join(run_dir_with_date,'namelist.wps')
                shutil.copyfile(src, dst)
                ## copy the geogrid table
                src = config['geogrid_tbl']
                assert os.path.exists(src), "Cannot find GEOGRID.TBL at {} ...".format(src)
                geogridFolder = os.path.join(run_dir_with_date,'geogrid')
                if not os.path.exists(geogridFolder):
                    mkdir_p(geogridFolder)
                ##
                dst = os.path.join(run_dir_with_date,'geogrid','GEOGRID.TBL')
                if os.path.exists(dst): os.remove(dst)
                os.symlink(src, dst)
                ## link to the geogrid.exe program
                src = config['geogrid_exe']
                assert os.path.exists(src), "Cannot find geogrid.exe at {} ...".format(src)
                dst = os.path.join(run_dir_with_date,'geogrid.exe')
                if not os.path.exists(dst):
                    os.symlink(src, dst)
                ## move to the directory and run geogrid.exe
                os.chdir(run_dir_with_date)
                print "\t\tRun geogrid at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                p = subprocess.Popen(['./geogrid.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = p.communicate()
                ##
                f = open('geogrid.log.stdout', 'w')
                f.writelines(stdout)
                f.close()
                ##
                f = open('geogrid.log.stderr', 'w')
                f.writelines(stderr)
                f.close()
                ## check that it ran
                dom = 'd0{}'.format(nDom)
                geoFile = 'geo_em.{}.nc'.format(dom)
                assert os.path.exists(geoFile), "./geogrid.exe did not produce expected output..."
                ##
                src = 'namelist.wps'
                dst = 'namelist.wps.geogrid'
                os.rename(src, dst)
                ## compress the output
                print "\tCompress the geo_em files"
                for iDom in range(nDom):
                    dom = 'd0{}'.format(iDom+1)
                    geoFile = 'geo_em.{}.nc'.format(dom)
                    compressNCfile(geoFile)
                    ## move the file to the namelist directory
                    src = os.path.join(run_dir_with_date, geoFile)
                    dst = os.path.join(config["nml_dir"], geoFile)
                    shutil.move(src, dst)
            ##
            ## link to the geo files
            for iDom in range(nDom):
                dom = 'd0{}'.format(iDom+1)
                geoFile = 'geo_em.{}.nc'.format(dom)
                ## move the file to the namelist directory
                src = os.path.join(config["nml_dir"], geoFile)
                dst = os.path.join(run_dir_with_date, geoFile)
                if not os.path.exists(dst):
                    os.symlink(src, dst)
            ##
            print "\tCheck that the met_em files exist"
            if not os.path.exists(config["metem_dir"]):
                mkdir_p(config["metem_dir"])
                metemFilesExist = False
            else:
                metemFilesExist = True
                ## check if the met_em files exist
                for hour in range(0,run_length_total_hours+1,6):
                    metem_time = job_start + datetime.timedelta(seconds = hour*3600)
                    metem_time_str = metem_time.strftime('%Y-%m-%d_%H:%M:%S')
                    for iDom in range(nDom):
                        dom = 'd0{}'.format(iDom+1)
                        metem_file = os.path.join(config["metem_dir"],'met_em.{}.{}.nc'.format(dom,metem_time_str))
                        metemFilesExist = metemFilesExist and os.path.exists(metem_file)
            ##
            if not metemFilesExist:
                print "\t\tThe met_em files did not exist - create them"
                ##
                os.chdir(run_dir_with_date)
                ## deal with SSTs first
                ##
                ## copy the link_grib script
                src = config["linkgrib_script"]
                assert os.path.exists(src), "Cannot find link_grib.csh at {} ...".format(src)
                dst = os.path.join(run_dir_with_date,"link_grib.csh")
                if os.path.exists(dst): os.remove(dst)
                os.symlink(src, dst)
                ## link the ungrib executabble
                src = config['ungrib_exe']
                assert os.path.exists(src), "Cannot find ungrib.exe at {} ...".format(src)
                dst = os.path.join(run_dir_with_date,"ungrib.exe")
                if not os.path.exists(dst):
                    os.symlink(src, dst)

                wpsStrDate = (job_start - datetime.timedelta(days = 1)).date()
                wpsEndDate = (job_end   + datetime.timedelta(days = 1)).date()
                nDaysWps = (wpsEndDate - wpsStrDate).days + 1

                ## should we use ERA-Interim analyses?
                if config['analysis_source'] == 'ERAI':

                    if config['use_high_res_sst_data']:
                        ## configure the namelist
                        ## EDIT: the following are the substitutions used for the WPS namelist
                        WPSnml['share']['start_date'] = [job_start.strftime('%Y-%m-%d_00:00:00')] * nDom
                        WPSnml['share']['end_date']   = [(job_end.date() + datetime.timedelta(days=1)).strftime(  '%Y-%m-%d_%H:%M:%S')] * nDom
                        WPSnml['share']['interval_seconds'] = 6*60*60 ## 24*60*60
                        WPSnml['ungrib']['prefix']    = 'SST'
                        ## end edit section #####################################################
                        ## write out the namelist
                        if os.path.exists('namelist.wps'): os.remove('namelist.wps')
                        ##
                        WPSnml.write('namelist.wps')

                        sstDir = 'sst_tmp'
                        if not os.path.exists(sstDir):
                            mkdir_p(sstDir)
                        ##
                        for iDayWps in range(nDaysWps):
                            wpsDate = wpsStrDate + datetime.timedelta(days = iDayWps)
                            ## check for the monthly file
                            monthlyFile = wpsDate.strftime(config["sst_monthly_pattern"])
                            monthlyFileSrc = os.path.join(config["sst_monthly_dir"], monthlyFile)
                            monthlyFileDst = os.path.join(sstDir, monthlyFile)
                            if os.path.exists(monthlyFileSrc) and (not os.path.exists(monthlyFileDst)):
                                if not os.path.exists(monthlyFileDst):
                                    os.symlink(monthlyFileSrc, monthlyFileDst)
                            ## check for the daily file
                            dailyFile = wpsDate.strftime(config["sst_daily_pattern"])
                            dailyFileSrc = os.path.join(config["sst_daily_dir"], dailyFile)
                            dailyFileDst = os.path.join(sstDir, dailyFile)
                            if os.path.exists(dailyFileSrc) and (not os.path.exists(dailyFileDst)):
                                if not os.path.exists(dailyFileDst):
                                    os.symlink(dailyFileSrc, dailyFileDst)
                        ##
                        wpsStrDateStr = wpsStrDate.strftime('%Y-%m-%d_%H:%M:%S')
                        wpsEndDateEnd = wpsEndDate.strftime('%Y-%m-%d_%H:%M:%S')
                        ##
                        purge(run_dir_with_date, 'GRIBFILE*')
                        print "\t\tRun link_grib for the SST data at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                        p = subprocess.Popen(['./link_grib.csh',os.path.join(sstDir,'*')], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        stdout, stderr = p.communicate()
                        ##
                        f = open('link_grib_sst.log.stdout', 'w')
                        f.writelines(stdout)
                        f.close()
                        ##
                        f = open('link_grib_sst.log.stderr', 'w')
                        f.writelines(stderr)
                        f.close()
                        ## check that it ran
                        ## time.sleep(0.2)
                        gribmatches = [f for f in os.listdir(run_dir_with_date) if re.search('GRIBFILE', f) > 0 ]
                        if len(gribmatches) == 0:
                            raise RuntimeError("Gribfiles not linked successfully...")
                        ## link to the SST Vtable
                        src = config["sst_vtable"]
                        assert os.path.exists(src), "SST Vtable expected at {}".format(src)
                        dst = 'Vtable'
                        if os.path.exists(dst): os.remove(dst)
                        os.symlink(src,dst)
                        purge(run_dir_with_date, 'SST:*')
                        purge(run_dir_with_date, 'PFILE:*')
                        ## run ungrib on the SST files
                        ## logfile = 'ungrib_sst.log'
                        ## output_f = open(logfile, 'w')
                        print "\t\tRun ungrib for the SST data at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                        p = subprocess.Popen(['./ungrib.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        stdout, stderr = p.communicate()
                        # output_f.flush()
                        ## output_f.close()
                        ## time.sleep(0.5)
                        ##
                        f = open('ungrib_sst.log.stdout', 'w')
                        f.writelines(stdout)
                        f.close()
                        ##
                        f = open('ungrib_sst.log.stderr', 'w')
                        f.writelines(stderr)
                        f.close()

                        ## check that it ran
                        ## matches = grep_file('Successful completion of ungrib', logfile)
                        matches = grep_lines('Successful completion of ungrib', stdout)
                        if len(matches) == 0:
                            raise RuntimeError("Success message not found in ungrib logfile...")

                        src = 'namelist.wps'
                        dst = 'namelist.wps.sst'
                        os.rename(src, dst)

                    analysisDir = 'analysis_tmp'
                    if not os.path.exists(analysisDir):
                        mkdir_p(analysisDir)

                    ## find the files matching the analysis pattern
                    patternTypes = ["analysis_pattern_surface", "analysis_pattern_upper"]
                    for patternType in patternTypes:
                        pattern = config[patternType]
                        files = set([])
                        for iDayWps in range(nDaysWps):
                            wpsDate = wpsStrDate + datetime.timedelta(days = iDayWps)
                            patternWithDates = wpsDate.strftime(pattern)
                            files = files.union(set(glob.glob(patternWithDates)))
                        ##
                        files = list(files)
                        files.sort()
                        if patternType == "analysis_pattern_upper":
                            ## for the upper-level files, be selective and use only those that contain the relevant range of dates
                            for ifile, filename in enumerate(files):
                                basepieces = os.path.basename(filename).split('_')
                                fileStartDateStr = os.path.basename(filename).split('_')[-2]
                                fileEndDateStr = os.path.basename(filename).split('_')[-1]
                                fileStartDate = datetime.datetime.strptime(fileStartDateStr,'%Y%m%d').date()
                                fileEndDate = datetime.datetime.strptime(fileEndDateStr,'%Y%m%d').date()
                                ##
                                if fileStartDate <= wpsStrDate and wpsStrDate <= fileEndDate:
                                    ifileStart = ifile
                                ##
                                if fileStartDate <= wpsEndDate and wpsEndDate <= fileEndDate:
                                    ifileEnd   = ifile
                        else:
                            ## for the surface files use all those that match
                            ifileStart = 0
                            ifileEnd = len(files)-1
                        ##
                        for ifile in range(ifileStart, ifileEnd+1):
                            src = files[ifile]
                            dst = os.path.join(analysisDir,os.path.basename(src))
                            if not os.path.exists(dst):
                                os.symlink(src, dst)

                        ## prepare to run link_grib.csh
                        linkGribCmds = ['./link_grib.csh',os.path.join(analysisDir,'*')]

                else:
                    ## consider the case that we are using the FNL datax
                    nIntervals = int(round((job_end - job_start).total_seconds()/3600./6.)) + 1
                    FNLtimes = [job_start + datetime.timedelta(hours=6*hi) for hi in range(nIntervals)]
                    FNLfiles = [time.strftime('gdas1.fnl0p25.%Y%m%d%H.f00.grib2') for time in FNLtimes]
                    ## if the FNL data exists, don't bother downloading
                    allFNLfilesExist = all([os.path.exists(FNLfile) for FNLfile in FNLfiles])
                    if allFNLfilesExist:
                        print "\t\tAll FNL files were found - do not repeat the download"
                    else:
                        ## otherwise get it all
                        FNLfiles = downloadFNL(email = config['rda_ucar_edu_email'],
                                               pswd = config['rda_ucar_edu_pword'],
                                               targetDir = run_dir_with_date,
                                               times = FNLtimes)
                    linkGribCmds = ['./link_grib.csh' ] + FNLfiles
                    ## optionally take a regional subset
                    if config['regional_subset_of_grib_data']:
                        geoFile = 'geo_em.d01.nc'
                        ## find the geographical region, and add a few degrees on either side
                        geoStrs = {}
                        nc = netCDF4.Dataset(geoFile)
                        for varname in ['XLAT_M', 'XLONG_M']:
                            coords = nc.variables[varname][:]
                            coords = [coords.min(),coords.max()]
                            coords = [math.floor((coords[0])/5.0 - 1)*5, math.ceil((coords[1])/5.0 + 1)*5 ]
                            coordStr = '{}:{}'.format(coords[0],coords[1])
                            geoStrs[varname] = coordStr
                        nc.close()
                        ## use wgrib2 that 
                        for FNLfile in FNLfiles:
                            tmpfile = os.path.join('/tmp',os.path.basename(FNLfile))
                            print "\t\tSubset the grib file",os.path.basename(FNLfile)
                            stdout, stderr = subprocess.Popen(['wgrib2',FNLfile,'-small_grib',geoStrs['XLONG_M'], geoStrs['XLAT_M'],tmpfile], stdout=subprocess.PIPE, stderr=subprocess.PIPE).communicate()
                            if len(stderr) > 0:
                                raise RuntimeError("Errors found when running wgrib2...")
                            ## use the subset instead - delete the original and put the subset in its place
                            os.remove(FNLfile)
                            shutil.copyfile(tmpfile,FNLfile)
                    
                ## write out the namelist
                if os.path.exists('namelist.wps'):
                    os.remove('namelist.wps')
                ##
                ## EDIT: the following are the substitutions used for the WPS namelist
                WPSnml['share']['start_date'] = [job_start.strftime('%Y-%m-%d_%H:%M:%S')] * nDom
                WPSnml['share']['end_date']   = [job_end.strftime(  '%Y-%m-%d_%H:%M:%S')] * nDom
                WPSnml['ungrib']['prefix'] = 'ERA'
                WPSnml['share']['interval_seconds'] = 6*60*60
                ## end edit section #####################################################
                ## write out the namelist
                if os.path.exists('namelist.wps'): os.remove('namelist.wps')
                ##
                WPSnml.write('namelist.wps')
                ##
                purge(run_dir_with_date, 'GRIBFILE*')
                print "\t\tRun link_grib for the ERA data at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                p = subprocess.Popen(linkGribCmds, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = p.communicate()
                ##
                f = open('link_grib_era.log.stdout', 'w')
                f.writelines(stdout)
                f.close()
                ##
                f = open('link_grib_era.log.stderr', 'w')
                f.writelines(stderr)
                f.close()

                ## check that it ran
                gribmatches = [f for f in os.listdir(run_dir_with_date) if re.search('GRIBFILE', f) > 0 ]
                if len(gribmatches) == 0:
                    raise RuntimeError("Gribfiles not linked successfully...")

                ###################
                # Run ungrib
                ###################

                ## link to the relevant Vtable
                src = config["analysis_vtable"]
                assert os.path.exists(src), "Analysis Vtable expected at {}".format(src)
                dst = os.path.join(run_dir_with_date,'Vtable')
                if os.path.exists(dst): os.remove(dst)
                os.symlink(src, dst)

                purge(run_dir_with_date, 'ERA:*')
                ## with open('ungrib.log.era', 'w') as output_f:
                print "\t\tRun ungrib for the ERA data at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                p = subprocess.Popen(['./ungrib.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = p.communicate()
                ##
                f = open('ungrib_era.log.stdout', 'w')
                f.writelines(stdout)
                f.close()
                ##
                f = open('ungrib_era.log.stderr', 'w')
                f.writelines(stderr)
                f.close()

                ## FIXME: check that it worked
                matches = grep_lines('Successful completion of ungrib', stdout)
                if len(matches) == 0:
                    raise RuntimeError("Success message not found in ungrib logfile...")

                ## if we are using the FNL analyses, delete the downloaded FNL files
                if config['analysis_source'] == 'FNL':
                    for FNLfile in FNLfiles:
                        os.remove(FNLfile)
                
                #############
                # Run metgrid
                #############
                print "\t\tRun metgrid at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
                metgriddir = os.path.join(run_dir_with_date,'metgrid')
                if not os.path.exists(metgriddir):
                    mkdir_p(metgriddir)
                WPSnml['metgrid']['fg_name']    = ['ERA']
                if config['use_high_res_sst_data']:
                    WPSnml['metgrid']['fg_name'].append('SST')
                ##
                ## link to the relevant METGRID.TBL
                src = config['metgrid_tbl']
                assert os.path.exists(src), "Cannot find METGRID.TBL at {} ...".format(src)
                dst = os.path.join(metgriddir,'METGRID.TBL')
                if not os.path.exists(dst):
                    os.symlink(src, dst)
                ## link to metgrid.exe
                src = config['metgrid_exe']
                assert os.path.exists(src), "Cannot find metgrid.exe at {} ...".format(src)
                dst = os.path.join(run_dir_with_date,'metgrid.exe')
                if not os.path.exists(dst):
                    os.symlink(src, dst)
                ##
                ## logfile = 'metgrid_stderr_stdout.log'
                ## with open(logfile, 'w') as output_f:
                p = subprocess.Popen(['./metgrid.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                stdout, stderr = p.communicate()
                ##
                f = open('metgrid.log.stdout', 'w')
                f.writelines(stdout)
                f.close()
                ##
                f = open('metgrid.log.stderr', 'w')
                f.writelines(stderr)
                f.close()
                
                matches = grep_lines('Successful completion of metgrid', stdout)
                if len(matches) == 0:
                    raise RuntimeError("Success message not found in metgrid logfile...")
                
                purge(run_dir_with_date, 'ERA:*')
                if config['use_high_res_sst_data']:
                    purge(run_dir_with_date, 'SST:*')
                purge(run_dir_with_date, 'FILE:*')
                purge(run_dir_with_date, 'PFILE:*')
                purge(run_dir_with_date, 'GRIB:*')
                purge(run_dir_with_date, 'fort.*')
                
                ## move the met_em files into the combined METEM_DIR directory
                move_pattern_to_dir(sourceDir = run_dir_with_date,
                                    pattern   = 'met_em*',
                                    destDir   = config["metem_dir"])
    
            ## link to the met_em files
            os.chdir(run_dir_with_date)
            print '\t\tlink to the met_em files'
            for hour in range(0,run_length_total_hours+1,6):
                metem_time = job_start + datetime.timedelta(seconds = hour*3600)
                metem_time_str = metem_time.strftime('%Y-%m-%d_%H:%M:%S')
                for iDom in range(nDom):
                    dom = 'd0{}'.format(iDom+1)
                    metem_file = 'met_em.{}.{}.nc'.format(dom,metem_time_str)
                    src = os.path.join(config["metem_dir"],metem_file)
                    assert os.path.exists(src), "Cannot find met_em file at {} ...".format(src)
                    dst = os.path.join(run_dir_with_date,metem_file)
                    if not os.path.exists(dst):
                        os.symlink(src, dst)

    ## find a met_em file and read the number of atmospheric and soil levels
    metempattern = os.path.join(config["metem_dir"],"met_em.d*.nc")
    ## 
    metemfiles = glob.glob(metempattern)
    assert len(metemfiles) > 0, "No met_em files found..."
    metemfile = metemfiles[0]
    nc = netCDF4.Dataset(metemfile)
    nz_metem = len(nc.dimensions['num_metgrid_levels'])
    nz_soil = len(nc.dimensions['num_st_layers'])
    nc.close()

    ## configure the WRF namelist
    print '\t\tconfigure the WRF namelist'
    ########## EDIT: the following are the substitutions used for the WRF namelist
    WRFnml['time_control']['start_year']   = [job_start.year]   * nDom
    WRFnml['time_control']['start_month']  = [job_start.month]  * nDom
    WRFnml['time_control']['start_day']    = [job_start.day]    * nDom
    WRFnml['time_control']['start_hour']   = [job_start.hour]   * nDom
    WRFnml['time_control']['start_minute'] = [job_start.minute] * nDom
    WRFnml['time_control']['start_second'] = [job_start.second] * nDom
    ##
    WRFnml['time_control']['end_year']     = [job_end.year]     * nDom
    WRFnml['time_control']['end_month']    = [job_end.month]    * nDom
    WRFnml['time_control']['end_day']      = [job_end.day]      * nDom
    WRFnml['time_control']['end_hour']     = [job_end.hour]     * nDom
    WRFnml['time_control']['end_minute']   = [job_end.minute]   * nDom
    WRFnml['time_control']['end_second']   = [job_end.second]   * nDom
    ########## end edit section #####################################################
    ##
    WRFnml['time_control']['restart']      = config['restart']
    ##
    WRFnml['domains']['num_metgrid_levels'] = nz_metem
    WRFnml['domains']['num_metgrid_soil_levels'] = nz_soil
    ##
    nmlfile = 'namelist.input'
    if os.path.exists(nmlfile): os.remove(nmlfile)
    WRFnml.write(nmlfile)
    ## 
    # Get real.exe and WRF.exe
    src = config['real_exe']
    assert os.path.exists(src), "Cannot find real.exe at {} ...".format(src)
    dst = os.path.join(run_dir_with_date,'real.exe')
    if os.path.exists(dst): os.remove(dst)
    os.symlink(src, dst)
    ##
    src = config['wrf_exe']
    assert os.path.exists(src), "Cannot find wrf.exe at {} ...".format(src)
    dst = os.path.join(run_dir_with_date,'wrf.exe')
    if os.path.exists(dst): os.remove(dst)
    os.symlink(src, dst)

    # Get tables
    link_pattern_to_dir(sourceDir = config['wrf_run_dir'],
                        pattern = config['wrf_run_tables_pattern'],
                        destDir = run_dir_with_date)

    # link to scripts from the namelist directory
    scriptsToGet = config['scripts_to_copy_from_nml_dir'].split(',')
    for scriptToGet in scriptsToGet:
        src = os.path.join(config["nml_dir"], scriptToGet)
        assert os.path.exists(src), "Cannot find script {} ...".format(scriptToGet)
        dst = os.path.join(run_dir_with_date, scriptToGet)
        if not os.path.exists(dst):
            os.symlink(src, dst)

    if (not config['only_edit_namelists']) and (not wrfInitFilesExist):
        ##
        logfile = 'real_stderr_stdout.log'
        
        print "\t\tRun real.exe at {}".format(datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'))
        p = subprocess.Popen(['mpirun','-np','1','./real.exe'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = p.communicate()
        ##
        f = open('real.log.stdout', 'w')
        f.writelines(stdout)
        f.close()
        ##
        f = open('real.log.stderr', 'w')
        f.writelines(stderr)
        f.close()

        rsloutfile = 'rsl.out.0000'
        matches = grep_file('SUCCESS COMPLETE REAL_EM INIT', rsloutfile)
        if len(matches) == 0:
            raise RuntimeError("Success message not found in real.exe logfile (rsl.out.0000)...")
        ##
        if os.path.exists('link_grib.csh'): os.remove('link_grib.csh')
        if os.path.exists('Vtable'): os.remove('Vtable')
        if os.path.exists('metgrid'): shutil.rmtree('metgrid')
        if os.path.exists('metgrid.exe'): os.remove('metgrid.exe')
        if os.path.exists('ungrib.exe'): os.remove('ungrib.exe')

        ## optionally delete the met_em files once they have been used
        if config['delete_metem_files']:
            purge(config["metem_dir"], 'met_em*')

    ## generate the run and cleanup scripts
    print '\t\tGenerate the run and cleanup script'

    ########## EDIT: the following are the substitutions used for the per-run cleanup and run scripts
    substitutions = {'RUN_DIR': run_dir_with_date,
                     'RUNSHORT': config["run_name"][:8],
                     'STARTDATE' : job_start_usable.strftime('%Y%m%d'),
                     'firstTimeToKeep': job_start_usable.strftime('%Y-%m-%d_%H:%M:%S')}
    ########## end edit section #####################################################

    ## write out the run and cleanup script
    for dailyScriptName in dailyScriptNames:
        ## do the substitutions
        thisScript = copy.copy(scripts[dailyScriptName])
        for avail_key in substitutions.keys():
            key = '${%s}' % avail_key
            value = substitutions[avail_key]
            thisScript = [ l.replace(key,value) for l in thisScript ]
        ## write out the lines
        scriptFile = '{}.sh'.format(dailyScriptName)
        scriptPath = os.path.join(run_dir_with_date, scriptFile)
        f = file(scriptPath,'w')
        f.writelines(thisScript)
        f.close()
        ## make executable
        os.chmod(scriptPath,os.stat(scriptPath).st_mode | stat.S_IEXEC) 
            
            
