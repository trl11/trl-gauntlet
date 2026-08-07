pipeline {
    // Release builds are portable across the Jenkins fleet; do not reserve the
    // two heavy agents while compatible capacity is available elsewhere.
    agent any

    options {
        ansiColor('xterm')
        timeout(time: 180, unit: 'MINUTES')
    }

    environment {
        CI = 'true'
        HOME = "${WORKSPACE}"
    }

    stages {
        stage('Setup') {
            steps {
                sh '''#!/usr/bin/env bash
                    set -euo pipefail
                    command -v uv >/dev/null || python3 -m pip install --user --disable-pip-version-check uv
                '''
                sshagent(credentials: ['github-ssh']) {
                    sh 'git submodule update --init --recursive'
                }
                // `pip install --user` honors HOME, keeping the bootstrap local
                // to this workspace rather than mutating a Jenkins agent image.
                sh 'PATH="$HOME/.local/bin:$PATH" make setup'
            }
        }

        stage('Check') {
            steps {
                sh 'PATH="$HOME/.local/bin:$PATH" make check'
            }
        }

        stage('Build release artifacts') {
            steps {
                sh 'PATH="$HOME/.local/bin:$PATH" make build'
                sh '''#!/usr/bin/env bash
                    set -euo pipefail
                    version=$(< VERSION)
                    expected=(
                      "dist/gauntlet-${version}.AppImage"
                      "dist/gauntlet-${version}.deb"
                      "dist/gauntlet-${version}-image.tar.gz"
                      "dist/gauntlet-${version}-py3-none-any.whl"
                      "dist/gauntlet_sdk-${version}-py3-none-any.whl"
                      "dist/README.txt"
                      "dist/setup-host.sh"
                      "dist/99-gauntlet-instruments.rules"
                    )
                    for artifact in "${expected[@]}"; do
                      test -s "$artifact"
                    done
                    test "$(find dist -maxdepth 1 -type f | wc -l)" -eq "${#expected[@]}"
                    printf 'Verified release artifacts:\n'
                    printf '  %s\n' "${expected[@]}"
                '''
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
