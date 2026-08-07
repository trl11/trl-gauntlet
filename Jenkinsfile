pipeline {
    // Every Jenkins worker has the `docker` label. The project CI container is
    // started explicitly because each worker's Docker socket has its own GID.
    // The host GID is resolved at runtime instead of assuming a fixed group.
    agent { label 'docker' }

    options {
        ansiColor('xterm')
        timeout(time: 180, unit: 'MINUTES')
    }

    environment {
        CI = 'true'
        HOME = "${WORKSPACE}"
        CI_IMAGE = 'ci-trl-gauntlet'
        APT_PROXY_URL = 'http://192.168.11.112:3142'
    }

    stages {
        stage('CI') {
            steps {
                script {
                    def socketGid = sh(
                        script: 'stat -c %g /var/run/docker.sock',
                        returnStdout: true,
                    ).trim()
                    def image = docker.build(
                        "${CI_IMAGE}:${BUILD_NUMBER}",
                        "--build-arg APT_PROXY_URL=${APT_PROXY_URL} --file ci/Dockerfile .",
                    )
                    def dockerArgs = "--group-add ${socketGid} " +
                        '-v /var/run/docker.sock:/var/run/docker.sock ' +
                        '-v /usr/bin/docker:/usr/bin/docker ' +
                        '-v /usr/libexec/docker:/usr/libexec/docker'
                    image.inside(dockerArgs) {
                        // Start the SSH agent after entering the CI container. Its UNIX
                        // socket is then local to the container, rather than a socket in
                        // the outer Jenkins agent namespace that Docker cannot bind here.
                        sshagent(credentials: ['github-ssh']) {
                            sh 'git submodule update --init --recursive'
                            sh 'make setup'
                            sh 'make check'
                            sh 'make build'
                            sh 'make ci-validate-dist'
                        }
                    }
                }
            }
        }
    }

    post {
        always {
            archiveArtifacts artifacts: 'dist/*', allowEmptyArchive: true, fingerprint: true
            junit allowEmptyResults: true, testResults: 'build/junit.xml'
            cleanWs(deleteDirs: true, patterns: [[pattern: '.git/**', type: 'EXCLUDE']])
        }
        regression {
            slackSend(
                color: 'danger',
                message: ":red_circle: *${env.JOB_NAME}* #${env.BUILD_NUMBER} - FAILURE\n<${env.BUILD_URL}|View Build>",
                notifyCommitters: true
            )
        }
        fixed {
            slackSend(
                color: 'good',
                message: ":large_green_circle: *${env.JOB_NAME}* #${env.BUILD_NUMBER} - FIXED\n<${env.BUILD_URL}|View Build>",
                notifyCommitters: true
            )
        }
    }
}
