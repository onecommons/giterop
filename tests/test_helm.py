import os
import os.path
import sys
import threading
import unittest
from functools import partial

from six.moves import urllib

from unfurl.job import JobOptions, Runner
from unfurl.yamlmanifest import YamlManifest


# http://localhost:8000/fixtures/helmrepo
@unittest.skipIf("helm" in os.getenv("UNFURL_TEST_SKIP", ""), "UNFURL_TEST_SKIP set")
class HelmTest(unittest.TestCase):
    def setUp(self):
        path = os.path.join(
            os.path.dirname(__file__), "examples", "helm-simple-ensemble.yaml"
        )
        with open(path) as f:
            self.manifest = f.read()

        server_address = ("", 8010)
        directory = os.path.dirname(__file__)
        try:
            if sys.version_info[0] >= 3:
                from http.server import HTTPServer, SimpleHTTPRequestHandler

                handler = partial(SimpleHTTPRequestHandler, directory=directory)
                self.httpd = HTTPServer(server_address, handler)
            else:  # for python 2.7
                import urllib

                import SocketServer
                from SimpleHTTPServer import SimpleHTTPRequestHandler

                class RootedHTTPRequestHandler(SimpleHTTPRequestHandler):
                    def translate_path(self, path):
                        path = os.path.normpath(urllib.unquote(path))
                        words = path.split("/")
                        words = filter(None, words)
                        path = directory
                        for word in words:
                            drive, word = os.path.splitdrive(word)
                            head, word = os.path.split(word)
                            if word in (os.curdir, os.pardir):
                                continue
                            path = os.path.join(path, word)
                        return path

                self.httpd = SocketServer.TCPServer(
                    server_address, RootedHTTPRequestHandler
                )
        except:  # address might still be in use
            self.httpd = None
            return

        t = threading.Thread(name="http_thread", target=self.httpd.serve_forever)
        t.daemon = True
        t.start()

    def tearDown(self):
        if self.httpd:
            self.httpd.socket.close()

    def test_deploy(self):
        # make sure this works
        f = urllib.request.urlopen("http://localhost:8010/fixtures/helmrepo/index.yaml")
        f.close()

        runner = Runner(YamlManifest(self.manifest))
        run1 = runner.run(JobOptions(planOnly=True, verbose=3, startTime=1))
        mysql_release = runner.manifest.rootResource.findResource("mysql_release")
        query = ".::.requirements::[.name=host]::.target::name"
        res = mysql_release.query(query)
        assert res == "unfurl-helm-unittest"

        runner = Runner(YamlManifest(self.manifest))
        run1 = runner.run(JobOptions(dryrun=False, verbose=3, startTime=1))
        assert not run1.unexpectedAbort, run1.unexpectedAbort.getStackTrace()
        summary = run1.jsonSummary()
        # runner.manifest.statusSummary()
        # print(summary)
        self.assertEqual(
            summary["job"],
            {
                "id": "A01110000000",
                "status": "ok",
                "total": 4,
                "ok": 4,
                "error": 0,
                "unknown": 0,
                "skipped": 0,
                "changed": 4,
            },
        )
        assert all(task["targetStatus"] == "ok" for task in summary["tasks"]), summary[
            "tasks"
        ]
        # runner.manifest.dump()

    def test_undeploy(self):
        runner = Runner(YamlManifest(self.manifest))
        # print('load');  runner.manifest.statusSummary()
        run = runner.run(JobOptions(workflow="check", startTime=2))
        summary = run.jsonSummary()
        assert not run.unexpectedAbort, run.unexpectedAbort.getStackTrace()

        # print('check'); runner.manifest.statusSummary()
        run2 = runner.run(
            JobOptions(workflow="undeploy", startTime=3, destroyunmanaged=True)
        )

        assert not run2.unexpectedAbort, run2.unexpectedAbort.getStackTrace()
        summary = run2.jsonSummary()
        # print('undeploy'); runner.manifest.statusSummary()

        # note! if tests fail may need to run:
        #      helm uninstall mysql-test -n unfurl-helm-unittest
        #  and kubectl delete namespace unfurl-helm-unittest

        # note: this test relies on stable_repo being place in the helm cache by test_deploy()
        # comment out the repository requirement to run this test standalone
        assert all(
            task["targetStatus"] == "absent" for task in summary["tasks"]
        ), summary["tasks"]
        self.assertEqual(
            summary["job"],
            {
                "id": "A01130000000",
                "status": "ok",
                "total": 3,
                "ok": 3,
                "error": 0,
                "unknown": 0,
                "skipped": 0,
                "changed": 3,
            },
        )
