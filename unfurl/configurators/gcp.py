import os
from ..configurator import Configurator
from ..support import Status, Priority


class CheckGooglCloudConnectionConfigurator(Configurator):
    def can_dry_run(self, task):
        # ok because this will also be called on the subtask
        return True

    def should_run(self, task):
        try:
            import google.auth
        except ImportError:
            task.logger.verbose("Skipping validation of Google Cloud connection: google.auth package not found")
            return Priority.ignore

        return Priority.critical

    def run(self, task):
        import google.auth

        # check environment vars instead of connection attributes
        # because we want to make sure they were set by the connection
        status = Status.ok
        if not os.getenv("GOOGLE_OAUTH_ACCESS_TOKEN"):
            try:
                credentials, project_id = google.auth.default()
                assert credentials
                if project_id:
                    task.logger.verbose('validated google auth using default project: "%s"', project_id)
                else:
                    task.logger.verbose("validated google auth")
                if project_id and not os.getenv("CLOUDSDK_CORE_PROJECT"):
                    os.environ["CLOUDSDK_CORE_PROJECT"] = project_id
            except google.auth.exceptions.DefaultCredentialsError:
                task.logger.error("unable to authenticate with Google Cloud")
                status = Status.error
        elif not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            # GOOGLE_OAUTH_ACCESS_TOKEN is set but GOOGLE_APPLICATION_CREDENTIALS isn't
            # create an unfurl service account for the connection if template for that exists
            if task.target.template.spec.get_template('unfurl_service_account'):
                task.logger.verbose("Creating a Google Cloud service account for Unfurl.")
                jobrequest, errors = task.update_instances(
                    dict(name='unfurl_service_account', template='unfurl_service_account'))
                job = yield jobrequest
                status = job.status
        else:
            # both present, give GOOGLE_APPLICATION_CREDENTIALS priority
            del os.environ["GOOGLE_OAUTH_ACCESS_TOKEN"]

        yield task.done(True, status=status)

