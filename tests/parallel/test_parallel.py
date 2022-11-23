"""
The purpose of this test module is to run the test in a suite parallely

Syntax:
  - tests:
      -test:
        name: Parallel run
        module: test_parallel.py
        parallel:
           - test:
              ...
           - test:
               ...
        desc: Running tests parallely

Requirement parameters
     ceph_nodes:    The list of node participating in the RHCS environment.
     config:        The test configuration
     parallel:      Consist of test which needs to be executed parallely

Entry Point:
    def run(**kwargs):
"""
import datetime
import importlib
import os
from time import sleep

from ceph.parallel import parallel
from utility.log import Log

c_order = 0


def run(**kwargs):
    results = {}
    parallel_tests = kwargs["parallel"]
    tc_list = kwargs["parallel_tc_details"]
    count = 0

    with parallel() as p:
        for test in parallel_tests:
            tc = tc_list[count]
            tc["comments"] = f"Concurrent Test | Execution order: {count+1}"
            p.spawn(execute, test, kwargs, results, tc)
            sleep(1)
            count += 1

    test_rc = 0
    for key, value in results.items():
        if value != 0:
            test_rc = value

    return test_rc


def execute(test, args, results: dict, tc):
    """
    Executes the test under parallel in module named 'Parallel run'  parallely.

    It involves the following steps
        - Importing of test module based on test
        - Running the test module

    Args:
        test: The test module which needs to be executed
        cluster: Ceph cluster participating in the test.
        config:  The key/value pairs passed by the tester.
        results: results in dictionary
        tc: Individual test case dictionary which will contain results

    Returns:
        int: non-zero on failure, zero on pass
    """

    test = test.get("test")
    config = test.get("config", dict())
    config.update(args["config"])
    file_name = test.get("module")
    mod_file_name = os.path.splitext(file_name)[0]
    test_mod = importlib.import_module(mod_file_name)
    testcase_name = test.get("name", "Test Case Unknown")
    tcs = args["tcs"]
    run_dir = args["run_dir"]

    log = Log(file_name)

    tc["log-link"] = log.configure_logger(testcase_name, run_dir)
    print("Test logfile location: {}".format(tc["log-link"]))

    start = datetime.datetime.now()

    rc = test_mod.run(
        ceph_cluster=args["ceph_cluster"],
        ceph_nodes=args["ceph_nodes"],
        config=config,
        test_data=args["test_data"],
        ceph_cluster_dict=args["ceph_cluster_dict"],
        clients=args["clients"],
    )

    elapsed = datetime.datetime.now() - start
    post_run_update(tc, tcs, elapsed, rc, log, test_mod)

    file_string = f"{testcase_name}"
    results.update({file_string: rc})
    for key, value in results.items():
        log.info(f"{key} test result is {'PASS' if value == 0 else 'FAILED'}")


def post_run_update(tc, tcs, elapsed, rc, log, test_mod):
    """
    Update Test case details dictionary keys with run results
    """
    global c_order
    tc["duration"] = elapsed
    tc["comments"] += f" | Completion order: {c_order + 1}"
    c_order += 1

    if rc == 0:
        tc["status"] = "Pass"
        msg = "Test {} passed".format(test_mod)
        log.info(msg)
        print(msg)
    else:
        tc["status"] = "Failed"
        msg = "Test {} failed".format(test_mod)
        log.info(msg)
        print(msg)
        jenkins_rc = 1

    tcs.append(tc)