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
        stage('Setup') {
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
                        // Start the SSH agent inside the CI container so its UNIX socket
                        // is usable for private submodule cloning.
                        sshagent(credentials: ['github-ssh']) {
                            sh 'git submodule update --init --recursive'
                            sh 'make setup'
                        }
                    }
                }
            }
        }
        stage('Verify') {
            steps {
                script {
                    def socketGid = sh(
                        script: 'stat -c %g /var/run/docker.sock',
                        returnStdout: true,
                    ).trim()
                    def dockerArgs = "--group-add ${socketGid} " +
                        '-v /var/run/docker.sock:/var/run/docker.sock ' +
                        '-v /usr/bin/docker:/usr/bin/docker ' +
                        '-v /usr/libexec/docker:/usr/libexec/docker'
                    docker.image("${CI_IMAGE}:${BUILD_NUMBER}").inside(dockerArgs) {
                        sh 'make check'
                    }
                }
            }
        }
        stage('Build') {
            steps {
                script {
                    def socketGid = sh(
                        script: 'stat -c %g /var/run/docker.sock',
                        returnStdout: true,
                    ).trim()
                    def dockerArgs = "--group-add ${socketGid} " +
                        '-v /var/run/docker.sock:/var/run/docker.sock ' +
                        '-v /usr/bin/docker:/usr/bin/docker ' +
                        '-v /usr/libexec/docker:/usr/libexec/docker'
                    docker.image("${CI_IMAGE}:${BUILD_NUMBER}").inside(dockerArgs) {
                        sh 'make build'
                        sh 'make ci-validate-dist'
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
